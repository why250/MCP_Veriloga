# MCP_Veriloga

VerilogA 文档检索 MCP 服务，基于 FastMCP + SQLite FTS5（BM25 关键词检索）实现。  
支持在 Cursor AI 中直接查询 VerilogA 语法文档、生成代码模板。**无需下载任何 AI 模型，完全离线可用。**

---

## 功能概览

| MCP 工具 | 功能 |
|---|---|
| `search_veriloga` | BM25 全文检索 VerilogA 文档（OVI 规范 + ADS 2025 帮助） |
| `show_page` | 获取某个文档的完整内容 |
| `list_sources` | 列出所有已索引的文档来源 |
| `get_veriloga_template` | 获取即用型 VerilogA 模块代码模板（电阻/电容/MOSFET/VCO 等） |

---

## 目录结构

```
MCP_Veriloga/
├── reference/                         # VerilogA 参考文档（已预置）
│   ├── OVI_VerilogA.pdf               # OVI 官方语言规范
│   ├── VerilogA Modeling.pdf          # 建模教程
│   ├── veriaref.pdf                   # 快速参考
│   └── veriloga in ADS2025/veriloga/  # ADS 2025 官方帮助页 (7 HTML)
├── server/
│   ├── main.py          # FastMCP 主入口，4 个 MCP 工具定义
│   ├── indexer.py       # PDF + HTML 解析、分块、SQLite FTS5 索引构建
│   ├── searcher.py      # BM25 检索逻辑（SQLite FTS5）
│   ├── templates.py     # VerilogA 代码模板库（12 种模型类型）
│   ├── requirements.txt # Python 依赖
│   └── index_cache/     # 运行时自动生成（search.db）
├── deploy/
│   ├── deploy_remote.sh      # Linux 远程一键部署脚本
│   └── veriloga-mcp.service  # systemd 服务单元文件
├── CLAUDE.md            # AI 助手上下文文档
└── README.md
```

---

## 环境要求

| 项目 | 要求 |
|---|---|
| Python | **3.10 或更高**（推荐 3.11 / 3.12） |
| 操作系统 | Windows 10+、Linux（Ubuntu 20.04+）、macOS 12+ |
| 网络 | **首次安装需联网**下载 pip 包；索引构建和运行完全离线 |
| 内存 | 构建索引峰值 **~100 MB**，服务运行时 **~20 MB** |

> **无需 GPU，无需下载 AI 模型**。全文搜索基于 SQLite FTS5（BM25），完全使用 Python 标准库，无任何重型依赖。

---

## 在新电脑上安装

### Windows

#### 1. 获取代码

**方式 A — Git clone（推荐）：**

```powershell
git clone https://github.com/<你的用户名>/MCP_Veriloga.git
cd MCP_Veriloga
```

**方式 B — 直接复制文件夹：**

将整个 `MCP_Veriloga/` 文件夹（含 `server/` 和 `reference/`）复制到目标机器，进入该目录。

#### 2. 确认 Python 版本

```powershell
python --version
# 应输出 Python 3.10.x 或更高
```

如未安装 Python，从 [python.org](https://www.python.org/downloads/) 下载安装，安装时勾选 **"Add Python to PATH"**。

#### 3. 创建虚拟环境

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> 若 PowerShell 提示脚本执行策略错误，先运行：
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

#### 4. 安装依赖

```powershell
pip install --upgrade pip
pip install -r server\requirements.txt
```

安装耗时约 1-2 分钟。依赖包极少（仅 fastmcp、pymupdf、beautifulsoup4、lxml），无需编译 C++ 扩展。

#### 5. 预构建文档索引（推荐）

```powershell
python server\main.py --build-index
```

构建过程：使用 **pymupdf** 逐页解析 PDF → 解析 HTML → 生成文本块 → 写入 SQLite FTS5 索引 → 保存到 `server\index_cache\search.db`

**耗时约 10–30 秒**，峰值内存约 100 MB。之后每次启动直接加载，约 1 秒。

> 如果跳过此步骤，第一次调用 MCP 工具时会自动触发构建。

#### 6. 启动 MCP 服务

```powershell
python server\main.py
```

看到如下输出表示启动成功：

```
[veriloga-help] Starting MCP server on 0.0.0.0:8097
[veriloga-help] First request will trigger document indexing if cache is missing.
```

SSE 端点地址：`http://localhost:8097/mcp/sse`

---

### Linux / macOS

#### 1. 获取代码

```bash
git clone https://github.com/<你的用户名>/MCP_Veriloga.git
cd MCP_Veriloga
```

#### 2. 确认 Python 版本

```bash
python3 --version
# 应输出 Python 3.10.x 或更高
```

Ubuntu/Debian 安装 Python 3.11：

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-pip
```

#### 3. 创建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### 4. 安装依赖

```bash
pip install --upgrade pip
pip install -r server/requirements.txt
```

#### 5. 预构建文档索引

```bash
python server/main.py --build-index
```

#### 6. 启动 MCP 服务

```bash
python server/main.py
# 自定义端口：
python server/main.py --port 8096
```

---

## 在 Cursor 中注册 MCP

编辑 Cursor 的 MCP 配置文件：

- **Windows**：`%USERPROFILE%\.cursor\mcp.json`（通常为 `C:\Users\<用户名>\.cursor\mcp.json`）
- **Linux/macOS**：`~/.cursor/mcp.json`

添加以下内容（如文件不存在则新建）：

```json
{
  "mcpServers": {
    "veriloga-help": {
      "url": "http://localhost:8097/mcp/sse"
    }
  }
}
```

修改后**重启 Cursor** 才能识别新的 MCP 服务。

### 验证是否连接成功

在 Cursor 聊天中输入：

- "列出所有 VerilogA 文档来源"
- "查一下 Verilog-A 的 ddt 运算符用法"
- "给我一个 NMOS 晶体管的 Verilog-A 模板"

---

## 迁移到远程 Linux 服务器

### 方式 A：一键部署脚本（Linux/macOS 本机执行）

```bash
# 在 MCP_Veriloga 项目根目录执行
bash deploy/deploy_remote.sh
```

脚本会自动：传输代码和文档 → 安装依赖 → 构建索引 → 注册并启动 systemd 服务。

默认目标：`mcp@172.16.4.25:/opt/mcp/veriloga-help`，按需修改脚本顶部变量。

### 方式 B：手动步骤

#### 1. 复制文件到服务器

将以下目录复制到服务器（如 `/opt/mcp/veriloga-help/`）：

```
server/      （Python 代码）
reference/   （文档原始文件）
```

#### 2. 安装依赖

```bash
cd /opt/mcp/veriloga-help
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r server/requirements.txt
```

#### 3. 构建索引

```bash
.venv/bin/python server/main.py --build-index
```

#### 4. 创建 systemd 服务（开机自启）

```bash
sudo cp deploy/veriloga-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable veriloga-mcp
sudo systemctl start veriloga-mcp
sudo systemctl status veriloga-mcp
```

#### 5. 更新 mcp.json

将本地 URL 改为远程地址：

```json
"veriloga-help": {
  "url": "http://172.16.4.25:8096/mcp/sse"
}
```

### 常用运维命令

```bash
# 查看服务状态
sudo systemctl status veriloga-mcp

# 查看实时日志
sudo journalctl -u veriloga-mcp -f

# 重启服务
sudo systemctl restart veriloga-mcp

# 重建索引（文档更新后执行）
cd /opt/mcp/veriloga-help
.venv/bin/python server/main.py --build-index
sudo systemctl restart veriloga-mcp
```

---

## 常见问题

**Q: `pip install` 失败**  
A: 先执行 `pip install --upgrade pip`，再重试。依赖包均为纯 Python 或提供预编译 wheel，无需本地 C++ 编译器。

**Q: 启动时报 `No module named 'fastmcp'`**  
A: 未激活虚拟环境。执行 `.venv\Scripts\Activate.ps1`（Windows）或 `source .venv/bin/activate`（Linux/macOS）。

**Q: Cursor 中提示 MCP 连接失败**  
A: 确认服务正在运行（命令行无报错），`mcp.json` 中 URL 端口与启动端口一致，并已重启 Cursor。

**Q: 索引构建报 `No chunks extracted`**  
A: 确认 `reference/` 目录存在且包含 PDF 文件和 HTML 文件，路径中有 `veriloga in ADS2025/veriloga/` 子目录。

**Q: 构建时某个 PDF 页面打印 WARNING 并跳过**  
A: 该页面包含复杂字体或图形导致文本提取失败，pymupdf 会自动跳过并继续处理其他页面，不影响整体索引。

---

## 可用代码模板

通过 `get_veriloga_template("<类型>")` 获取：

| 模板名称 | 别名 | 说明 |
|---|---|---|
| `resistor` | r, res | 线性电阻 |
| `capacitor` | c, cap | 线性电容 |
| `inductor` | l, ind | 线性电感 |
| `diode` | d | Shockley 二极管 |
| `vccs` | gm | 压控电流源 |
| `vcvs` | gain | 压控电压源 |
| `nmos_simple` | nmos, mosfet | Level-1 NMOS 晶体管 |
| `opamp_ideal` | opamp | 理想单极点运放 |
| `vco` | oscillator | 压控振荡器 |
| `transmission_line` | tline | 无损耗传输线 |
| `noise_source` | noise | 热噪声电阻 |
| `pll_phase_detector` | pfd | 鉴相鉴频器 (PFD) |

---

## 技术细节

- **框架**: [FastMCP](https://github.com/jlowin/fastmcp) v2+，HTTP SSE 传输
- **全文检索**: SQLite FTS5 内置 BM25 排名，`sqlite3` 标准库，**零额外依赖**
- **PDF 解析**: [pymupdf](https://pymupdf.readthedocs.io/)（fitz）— C 底层 MuPDF，速度快、无无限循环问题
- **HTML 解析**: BeautifulSoup4 + lxml
- **索引格式**: 单个 SQLite 数据库文件 `index_cache/search.db`
- **分块大小**: ~500 字符，80 字符重叠
- **内存占用**: 构建峰值 ~100 MB，运行时 ~20 MB（仅 SQLite page cache）
- **文档来源**: 3 份 PDF（OVI 规范、建模教程、快速参考）+ 7 份 ADS 2025 HTML 帮助页
