---
name: Index Freeze Diagnosis Fix
overview: 用 pymupdf 替换 pypdf（根治 PDF 解析卡死），同时加固 SVD 词汇上限防止将来内存膨胀。
todos:
  - id: requirements
    content: "requirements.txt: 删除 pypdf>=4.0，添加 pymupdf>=1.24"
    status: pending
  - id: indexer-pdf
    content: "indexer.py: 用 fitz (pymupdf) 重写 _parse_pdf()，每页打印进度，保留 MAX_PAGE_CHARS 截断和 GC"
    status: pending
  - id: indexer-svd
    content: "indexer.py: 新增 MAX_FEATURES_NORMAL=80000，正常模式也加词汇上限；TruncatedSVD n_iter=3"
    status: pending
  - id: readme-update
    content: "README.md: 更新依赖说明和构建索引章节，添加卡死排查提示"
    status: pending
isProject: false
---

# Index 构建卡死：修复计划（pymupdf 方案）

## 根因

`--low-memory` 只降低 SVD 参数，不影响 PDF 解析，仍然卡死，证明卡死在 `_parse_pdf()` 阶段：

pypdf 的 `extract_text()` 在处理含**循环 ToUnicode CMap 或嵌套 Type0 字体**的 PDF 页时会无限循环，既不报错也不返回。`veriaref.pdf`（3.3 MB，最大文件）是最可疑的来源。

## 修复策略

### 修复 1（必须）— 换用 pymupdf

pymupdf（`fitz`）是 C 底层的 MuPDF 绑定，内置超时和字体容错，提取速度比 pypdf 快 5-10 倍，不存在 CMap 无限循环问题。

`**[server/requirements.txt](server/requirements.txt)`**：

```
删除：pypdf>=4.0
新增：pymupdf>=1.24
```

`**[server/indexer.py](server/indexer.py)**`，替换整个 `_parse_pdf()`：

```python
def _parse_pdf(pdf_path: Path) -> List[tuple[str, str]]:
    import fitz  # pymupdf
    pages = []
    try:
        doc = fitz.open(str(pdf_path))
        n = doc.page_count
        for i in range(n):
            print(f"    page {i+1}/{n}", end="\r", flush=True)
            try:
                page = doc[i]
                raw = page.get_text() or ""
            except Exception as exc:
                print(f"\n    WARNING: page {i+1}/{n} failed: {exc}")
                continue
            if len(raw) > MAX_PAGE_CHARS:
                raw = raw[:MAX_PAGE_CHARS]
            text = _clean_text(raw)
            del raw
            if text.strip():
                pages.append((f"Page {i+1}", text))
            if i % 50 == 49:
                gc.collect()
        print()  # 换行，结束 \r 进度行
    except Exception as exc:
        print(f"    WARNING: failed to open {pdf_path.name}: {exc}")
    finally:
        try:
            doc.close()
        except Exception:
            pass
        gc.collect()
    return pages
```

同时删除顶部的 `from pypdf import PdfReader` 行（改为在函数内 `import fitz`）。

### 修复 2（推荐）— 正常模式加词汇上限

`**[server/indexer.py](server/indexer.py)**` 顶部新增常量：

```python
MAX_FEATURES_NORMAL = 80_000
```

`_build_lsa_faiss()` 中：

- 正常模式 `max_features` 从 `None` → `MAX_FEATURES_NORMAL`
- `TruncatedSVD(n_iter=3, ...)` 替代默认 `n_iter=5`

峰值内存从不可控降至约 1 GB，检索质量几乎无损。

## 需改动的文件

- `[server/requirements.txt](server/requirements.txt)` — 替换依赖
- `[server/indexer.py](server/indexer.py)` — 替换 `_parse_pdf()` + 词汇上限
- `[README.md](README.md)` — 更新安装说明和排查提示

## 验证步骤

1. `pip install pymupdf` 安装新依赖
2. `python server/main.py --build-index --low-memory` — 应看到逐页进度，不再卡死
3. 确认 `index_cache/` 下生成 4 个文件后，再运行正常模式
4. `python server/main.py` 启动服务，Cursor 中调用 `search_veriloga` 验证检索正常

