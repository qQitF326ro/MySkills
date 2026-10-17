# Windows、打包与编码

适用范围：脚本在 Windows 下运行、需要打包为 EXE、涉及中文文本、文件枚举排序或多进程时使用。打包细节见 `references/pyinstaller.md`。

## Windows / multiprocessing

涉及 multiprocessing 或打包 EXE 时必须检查 `if __name__ == "__main__":`。Windows 使用 spawn 时注意：worker 可被子进程正常导入、不在模块 import 阶段创建进程、必要时 `multiprocessing.freeze_support()`、子进程数量受控、避免 EXE 递归启动自身、检查 PyInstaller 打包后的行为。

需同时验证 `.py` 与 `.exe` 在以下方面一致：脚本/EXE 所在目录、同目录 INI、日志、临时目录、subprocess、multiprocessing、第三方库资源。

## 路径兼容

使用 `sys.frozen` 判断运行环境：`.py` 用 `__file__` 所在目录，PyInstaller EXE 用 `sys.executable` 所在目录。读取脚本同级文件时以此目录为基准，不依赖当前工作目录。

程序名要兼容两类来源：`.py` 用 `__file__`、EXE 用 `sys.executable`。由程序名派生的 logger 名不要带点号（脚本名含 `v1.0` 这类版本号时会形成 logger 层级）。

## 文件枚举与排序（必须与资源管理器一致）

枚举顺序直接决定结果顺序（图片序列、页码、章节号、图层合并），因此固定为「与 Windows 资源管理器一致」：

- 数字段按**数值**比较：`img2.jpg` 排在 `img10.jpg` 之前（这是字典序做不到的关键点）；
- 其余部分不区分大小写（`img2` 与 `IMG2` 同级）；
- 数值相同时（`p1.txt` 与 `p01.txt`）资源管理器本身不保证顺序，本规范用原字符串做字典序 tiebreak，保证结果稳定、可复现；
- Windows 上调用 `shlwapi.StrCmpLogicalW`，与资源管理器完全一致；非 Windows 或 API 不可用时退回等价的纯 Python 自然排序 key。

禁止的写法：

```python
sorted(source_dir.iterdir())        # 字典序：img10 排在 img2 之前
for path in source_dir.iterdir():   # 依赖文件系统返回顺序，不稳定
os.listdir(source_dir)              # 同上
```

正确写法：

```python
from natsort_key import sorted_paths

for path in sorted_paths(source_dir.iterdir()):
    ...
```

实现位置：

- `scripts/natsort_key.py`：可导入、可命令行调用，带 `--self-test`；
- `assets/template.py`：内置等价实现 `explorer_sort_key()` / `sorted_paths()`。模板必须能自包含成单文件 EXE，因此这部分是有意重复的，修改时要同步两处。

验证方法：对 `["img10.jpg", "img2.jpg", "img1.jpg", "IMG3.jpg", "a1b10.txt", "a1b2.txt"]` 排序，结果必须是 `a1b2.txt, a1b10.txt, img1.jpg, img2.jpg, IMG3.jpg, img10.jpg`。运行 `python scripts/natsort_key.py --self-test` 可直接验证。

## INI 配置

配置文件默认与脚本同名 `脚本名.ini`，使用 `configparser`。

- 读取统一走 `read_text_auto()`（`utf-8-sig` → `gb18030`）。带 BOM 的 INI 必须能正常解析，否则会直接抛 `MissingSectionHeaderError`；
- 写入明确指定 `encoding="utf-8"`，且不写 BOM；
- 不要把已存在的 GB2312 配置静默改写为 UTF-8；确需改写时保持原编码或先备份；
- 配置不存在时根据需求创建并停止运行，配置项使用中文注释。

## 编码

- 文本读取优先 `utf-8-sig`（同时兼容带与不带 BOM），回退 `gb18030`；
- `gb18030` 是 GB2312 / GBK 的超集，不要只用 `gb2312`；
- 不要用 `errors="ignore"` 掩盖未知编码问题，可能静默丢数据；写文件必须明确指定 encoding。
- 控制台是另一件事：Windows 中文控制台通常是 GBK / cp936，日志或 `print` 中的 ✓、emoji、生僻字会触发 `UnicodeEncodeError`。启动时用 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；
- `pythonw.exe` / PyInstaller `--noconsole` 下 `sys.stdout` 与 `sys.stderr` 都是 `None`，此时必须跳过控制台 handler，否则 logging 会回退到 `sys.stderr` 并在写入时报错。

## UNC 与长路径

- 判断网络路径不能只看开头是不是 `\\`：`\\?\C:\...`（长路径前缀）与 `\\.\...`（设备命名空间）都是本地路径；
- 超过 255 字符时使用 `\\?\` 前缀，UNC 需要写成 `\\?\UNC\server\share\...`；
- UNC 路径下不要删除文件，替换前先备份到脚本所在目录（见 `references/data-integrity.md`）。

## 打包

默认按**单文件**（`--onefile`）打包；只有用户明确要求单目录时才使用 `--onedir`。完整约定、可复用 spec 骨架与打包自检清单见 `references/pyinstaller.md`。
