---
name: pymupdf page timeout fix
overview: 为 _parse_pdf() 的 page.get_text() 调用加入线程超时，同时用更简单的文本提取标志降低卡死概率，彻底解决单页卡死问题。
todos:
  - id: add-helper
    content: "indexer.py: 新增 _PDF_PAGE_TIMEOUT 常量和 _get_page_text() 辅助函数（简化 flags + 线程超时）"
    status: pending
  - id: update-parse-pdf
    content: "indexer.py: _parse_pdf() 循环内替换 doc[i].get_text() 为 _get_page_text(doc[i])，超时时打印 WARNING"
    status: pending
isProject: false
---

# pymupdf 单页卡死修复计划

## 根因

pymupdf 的 `page.get_text()` 在处理含**循环 XObject 引用或复杂向量图形**的 PDF 页时，即使是 C 层也会进入无限处理。由于没有超时机制，进程永久阻塞。

## 修复策略（两层防护）

### 防护 1 — 简化文本提取模式

将 `page.get_text()` 改为带 flags 的精简模式，禁用布局还原和图形处理，只提取原始文字流：

```python
import fitz
TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
raw = doc[i].get_text("text", flags=TEXT_FLAGS) or ""
```

`TEXT_MEDIABOX_CLIP` 裁掉页面边界外的隐藏内容（常见于复杂 PDF），`TEXT_PRESERVE_WHITESPACE` 保留空白字符结构，两个 flag 共同减少触发复杂渲染路径的概率。

### 防护 2 — 线程超时兜底

即使简化 flags 仍无法完全避免挂起，加入 `concurrent.futures` 线程超时作为最终保障：

```python
import concurrent.futures

_PDF_PAGE_TIMEOUT = 20  # 单页最长等待秒数

def _get_page_text(page) -> str:
    flags = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(page.get_text, "text", flags=flags)
        try:
            return future.result(timeout=_PDF_PAGE_TIMEOUT) or ""
        except concurrent.futures.TimeoutError:
            return ""
```

在 `_parse_pdf()` 的循环中调用 `_get_page_text(doc[i])` 替代 `doc[i].get_text()`，超时时打印 WARNING 并继续下一页：

```python
for i in range(n):
    print(f"    page {i+1}/{n}", end="\r", flush=True)
    try:
        raw = _get_page_text(doc[i])
        if not raw:
            continue
    except Exception as exc:
        print(f"\n    WARNING: page {i+1}/{n} failed: {exc}")
        continue
```

## 需改动的文件

- `[server/indexer.py](server/indexer.py)` — 新增 `_get_page_text()` 辅助函数；修改 `_parse_pdf()` 循环内的调用；更新 docstring

## 验证步骤

1. 运行 `python server\main.py --build-index --low-memory`
2. 应看到每个 PDF 的逐页进度持续推进，不再卡在某页
3. 如果某页超时，会打印 `WARNING: page X/N timed out, skipping` 并继续
4. 完成后 `index_cache\` 下生成 4 个文件，启动 MCP 服务验证检索正常

