# PyInstaller 打包（默认单文件）

适用范围：需要把 Python 脚本交付为独立 EXE 时使用。

## 打包形态：默认单文件

**默认 `--onefile`（单文件）。** 只有用户明确要求「单目录 / 不要单文件 / 启动快点」时才用 `--onedir`。

| 形态 | 何时用 | 代价 |
| --- | --- | --- |
| `--onefile`（默认） | 交付给用户单文件运行、放 U 盘、发邮件 | 每次启动解压到 `%TEMP%\_MEIxxxxx`，启动慢；杀软逐个扫描，易误报；Win7 上解压更慢 |
| `--onedir` | 明确要求；或依赖体积大、启动速度敏感、需要现场替换 DLL/模型 | 交付物是目录，需整体拷贝，不能只发 exe |

不要因为体积或启动速度就擅自改成 `--onedir`；这两种形态的资源加载路径不同，改形态属于交付内容变更。

## 资源路径：EXE 里没有脚本目录

单文件模式下运行时 `__file__` 指向解压出来的临时目录，随包资源要用 `sys._MEIPASS`：

```python
import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    """读取随包资源：源码运行用脚本目录，EXE 用解压目录。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent
    return base / relative
```

区分两类路径，不要混用：

- **随包只读资源**（模型、图标、模板）：用 `resource_path()`；
- **用户可写文件**（INI、日志、失败清单、输出）：用 `get_app_dir()` 即 `sys.executable` 所在目录，绝不能写到 `_MEIPASS`（退出即删）。

## 可复用 spec 骨架

把 `build.spec` 放在项目根目录，用 `pyinstaller build.spec --clean --noconfirm` 构建。显式写死资源清单，比命令行参数更容易维护与复查。

```python
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

APP_NAME = "批量重命名"
ENTRY = "batch_rename.py"

datas = [
    # (本地路径, 打包内相对目录)
    ("assets/模板.xlsx", "assets"),
]
binaries = []
hiddenimports = []

# 带数据文件或二进制扩展的包，用 collect_all 一次性收集
for package in ("pikepdf",):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [ENTRY],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name=APP_NAME,
    console=True,      # 无控制台需求时改 False，注意 sys.stdout 会变成 None
    upx=False,         # 关闭 UPX：杀软误报与个别 DLL 损坏
    onefile=True,      # 默认单文件；改成 False 即单目录
    icon="assets/app.ico",
)
```

要点：

- `console=False`（`--noconsole`）后 `sys.stdout` / `sys.stderr` 均为 `None`，日志必须只写文件（模板 `get_console_stream()` 已处理）；
- `upx=False`：UPX 压缩是杀软误报的高发原因，且可能损坏部分 DLL，除非明确需要压体积；
- `--clean --noconfirm`：避免复用旧缓存导致「改完代码行为没变」的假象；
- 版本信息 `--version-file`、图标 `--icon` 属于交付物外观，按需要加；
- 依赖含 OCR/PDF 库（onnxruntime、pikepdf、PyMuPDF）时，优先用 `collect_all` 处理，而不是手写 `--hidden-import` 逐个试。

## Windows 7 兼容

Win7 上是硬约束，打包前逐条确认：

- Python 版本上限 **3.8**（3.9+ 不支持 Win7）；
- 代码不要用 3.9+ 语法/API：`dict1 | dict2`、`str.removeprefix()`、`math.lcm()`、`Path.is_relative_to()`、`match` 语句、`functools.cache`（3.9 起才有）等；
- 运行依赖 `api-ms-win-crt-*`：目标机需装 VC++ 2015-2019 运行库，或随包携带对应 DLL；
- 打包环境本身最好就用 Win7 可用的解释器构建（例如项目里的 `build_win7` 环境），不要用 3.11 构建再指望在 Win7 跑；
- 单文件在 Win7 上首次解压更慢，且更容易被杀软拦截，需要时加白名单或改用 `--onedir`（仅在用户同意时）。

## 打包自检清单

`.py` 与 `.exe` 行为必须一致，逐项验证：

```text
1. 从资源管理器双击、从命令行、从其他工作目录启动，三种方式行为一致
2. 同目录 INI：首次运行生成、二次运行读取正常
3. 日志写到 EXE 所在目录，双日志分流正确，中文不乱码
4. 临时目录、失败清单 failed_*.csv 出现在 EXE 所在目录
5. subprocess 调用外部程序正常（注意打包后 cwd 与 PATH 变化）
6. multiprocessing / ProcessPoolExecutor 不出现递归启动（freeze_support）
7. 第三方库资源（模型、字典、字体）能加载；EXE 里没有相对路径假设
8. --noconsole 形态下异常仍写入错误日志，且不弹窗、不静默退出
9. 在目标机（含 Win7 测试机）实机运行，而不是只在开发机验证
10. 记录产物哈希与体积：certutil -hashfile dist\应用名.exe SHA256
```

构建产物与源码分离：`dist/`、`build/`、`*.spec` 的中间产物不要混入交付目录，`build_win37/` 这类旧构建目录定期归档。
