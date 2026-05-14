# 打包说明（Windows / macOS）

本文说明从**拿到本仓库源代码**开始，在 **Windows** 与 **macOS** 上安装依赖并完成 **PyInstaller** 打包的完整步骤。

> **重要**：必须在**目标操作系统本机**打包。无法在 Windows 上生成 macOS 可执行文件，反之亦然。  
> **例外**：可用 **GitHub Actions 等云端 macOS** 替你打包，见下文「〇」。

---

## 〇、Mac 上打包嫌麻烦？更省事 /「邪修」办法

### 〇.1 推荐：云端 Mac 自动打包（不用自己的 Mac）

仓库已带 [`.github/workflows/build-macos.yml`](../.github/workflows/build-macos.yml)：在 **GitHub** 上打开本仓库 → **Actions** → 选择 **「Build macOS」** → **Run workflow**（或打 `v*` 开头的 tag 也会触发）。

跑完后在对应 Run 页面下载 **Artifact `chatglm-macos-dist`**，解压即得到 `dist` 里的 macOS 可执行文件。你本地仍是 Windows 日常开发即可。

前提：代码已推到 GitHub，且 Actions 对仓库可用（公开仓无门槛；私有仓需配额/权限）。

### 〇.2 不打包：直接跑源码（适合自己会开终端的人）

不生成 `.app` / 单文件，只装依赖后运行：

```bash
cd chatglm-cn
python3.13 -m venv .venv && source .venv/bin/activate   # Windows 用 .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

有 [uv](https://github.com/astral-sh/uv) 时可更短：`uv run --python 3.13 python main.py`（需在项目里配好依赖或仍先 `uv pip install -r requirements.txt`）。**用户需要本机 Python**，但省去 PyInstaller 和签名一堆事。

### 〇.3「邪修」只发 Windows 版

- Mac 用户用 **Parallels / VMware / 云主机 Windows** 跑你已打好的 `.exe`。  
- 或只维护 **Windows 安装包**，文档写「macOS 请用浏览器 + 手动操作 / 远程一台 Windows」。  
- 适合「团队里全是 Win」或「个人自用一台云 Win」的场景。

### 〇.4 关于 macOS 签名与「未知开发者」

云端打出来的二进制**一样**可能触发门禁；想少弹窗要走 **Apple 开发者账号 + codesign + notarize**，和是否本地打包无关。个人小工具一般靠用户右键「打开」或系统设置里允许一次即可。

---

## 一、环境与依赖说明

### 1.1 Python 版本

- 项目要求：**Python 3.13+**（见 `pyproject.toml` 中 `requires-python`）。
- 请从 [python.org](https://www.python.org/downloads/) 或本机包管理器安装对应版本。

### 1.2 需要安装的 Python 库

与 `pyproject.toml` / `requirements.txt` 一致：

| 库 | 用途 |
|----|------|
| `playwright` | 控制浏览器自动化 |
| `send2trash` | 上传后将原图移入回收站（可选但推荐） |
| `pyinstaller` | 打包为独立可执行文件 |

标准库 **tkinter** 用于界面：Windows 自带；macOS 若使用 **python.org** 安装包一般自带 Tcl/Tk，若用 **Homebrew** 的 `python@3.13`，需确认该配方是否带 `tcl-tk`（无 tk 时需换用带 Tk 的 Python 构建）。

### 1.3 运行/打包后终端用户环境

- 当前程序使用 **系统已安装的 Google Chrome**（`channel="chrome"`），**不**依赖 Playwright 自带的 Chromium 浏览器包。
- 打包时已通过 `main.spec` 将 **Playwright 的 Node driver**（`playwright/driver`）打入产物，**最终用户无需安装 Python**。
- 用户机器上仍需安装 **Chrome**，并能正常打开智谱图生视频页。

---

## 二、获取源代码

```bash
git clone <仓库地址> chatglm-cn
cd chatglm-cn
```

若使用 zip 解压，进入解压后的项目根目录（应能看到 `main.py`、`main.spec`、`pyproject.toml`、`chatglm_video/` 等）。

---

## 三、Windows 打包步骤

### 3.1 进入项目根目录

```powershell
cd D:\path\to\chatglm-cn
```

（将路径换成你的实际目录。）

### 3.2 创建虚拟环境（推荐）

**方式 A：使用 `venv`（系统已安装 Python 3.13）**

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

**方式 B：使用 `uv`**

```powershell
uv venv --python 3.13
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
```

### 3.3 执行打包

确保已激活虚拟环境，且在**项目根目录**（存在 `main.spec`）：

```powershell
python -m PyInstaller main.spec --noconfirm
```

未激活 venv 时可直接指定解释器：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller main.spec --noconfirm
```

### 3.4 产物位置与名称

- 输出目录：**`dist\`**
- 可执行文件名由 `main.spec` 中 `EXE(..., name="...")` 决定；当前为 **`智谱图生视频.exe`**
- 若需英文名，修改 `main.spec` 第 42 行 `name=` 后重新执行上述命令。

### 3.5 清理旧构建（可选）

```powershell
rmdir /s /q build dist
python -m PyInstaller main.spec --noconfirm
```

### 3.6 中文文件名与终端乱码

- 资源管理器中应能正常显示中文 `.exe` 名称。
- 若 PowerShell 日志里中文显示为乱码，多为控制台代码页问题，**一般不影响生成的 exe**。
- 打包前可设置：`chcp 65001` 或使用 Windows Terminal，便于阅读 PyInstaller 输出。

---

## 四、macOS 打包步骤

必须在 **Mac 本机**（或 macOS CI 虚拟机）上操作；与 Windows 使用**同一份** `main.spec` 即可。

### 4.1 系统依赖

- **Xcode Command Line Tools**（编译部分原生扩展时可能需要）：

  ```bash
  xcode-select --install
  ```

- **Google Chrome**（与程序内 `channel="chrome"` 一致）。

- **带 Tcl/Tk 的 Python 3.13**：建议从 [python.org macOS installer](https://www.python.org/downloads/macos/) 安装；若 `import tkinter` 报错，请换用官方安装包或配置好 tk 的 Python。

### 4.2 进入项目根目录

```bash
cd /path/to/chatglm-cn
```

### 4.3 创建虚拟环境并安装依赖

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
```

使用 `uv` 时示例：

```bash
uv venv --python 3.13
source .venv/bin/activate
uv pip install -r requirements.txt
```

### 4.4 执行打包

```bash
python -m PyInstaller main.spec --noconfirm
```

### 4.5 产物位置与运行方式

- 输出目录：**`dist/`**
- macOS 下通常为**无后缀的可执行文件**，名称与 `main.spec` 中 `name` 一致（例如 `智谱图生视频`）。
- 首次从 Finder 外运行若被拦截，可在「系统设置 → 隐私与安全性」中允许，或在终端：

  ```bash
  chmod +x dist/智谱图生视频
  ./dist/智谱图生视频
  ```

### 4.6 关于 `.app` 与代码签名

- 当前 `main.spec` 生成的是**单文件可执行程序**，不是双击即用的 `.app` 目录结构。
- 若需分发给他人并减少「未知开发者」提示，需自行准备 **Apple 开发者证书** 做 **codesign / notarize**，此流程超出本仓库默认配置，需单独处理。

---

## 五、`main.spec` 简要说明

- **入口脚本**：`main.py`
- **数据文件**：将当前 Python 环境中 `playwright/driver` 目录打入包内，供运行时启动 Playwright Node 服务。
- **`hiddenimports`**：补充 `playwright`、`greenlet`、`pyee`、`send2trash` 等，减少运行期缺模块问题。
- **`console=False`**：无控制台黑窗（GUI 程序）。

修改应用显示名称或输出文件名：编辑 **`main.spec`** 中 `EXE(..., name="你的名称")`，保存后重新执行 `PyInstaller main.spec`。

---

## 六、常见问题

| 现象 | 处理建议 |
|------|----------|
| `ModuleNotFoundError: playwright` | 在**当前用于打包的 venv** 内执行 `pip install -r requirements.txt` |
| 打包成功但运行报错找不到 driver | 勿删 `main.spec` 中 `playwright/driver` 的 `datas` 段；确保打包用的环境与运行测试一致 |
| Mac 上 `import tkinter` 失败 | 换用 python.org 官方 pkg 或安装带 tk 的 Python |
| 杀毒软件报毒 | PyInstaller 单文件常被启发式误报，可加入白名单或换用代码签名（Windows EV 证书等） |

---

## 七、命令速查

| 步骤 | Windows (PowerShell) | macOS (bash/zsh) |
|------|------------------------|------------------|
| 创建 venv | `py -3.13 -m venv .venv` | `python3.13 -m venv .venv` |
| 激活 venv | `.\.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
| 安装依赖 | `pip install -r requirements.txt` | 同上 |
| 打包 | `python -m PyInstaller main.spec --noconfirm` | 同上 |
| 产物 | `dist\智谱图生视频.exe` | `dist/智谱图生视频`（名称以 spec 为准） |

完成以上步骤即视为「从源代码到可分发产物」的完整打包流程。
