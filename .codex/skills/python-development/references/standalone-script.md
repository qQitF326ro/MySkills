# 新建独立脚本脚手架

适用范围：从零编写需要交付为独立程序（常为单文件 EXE）的脚本时，用来建立与 `assets/template.py` 一致的公共结构。

## 模板复用

如果存在 `assets/template.py`，新建前先读取它。若模板已包含路径处理、配置读写、日志、retry、I/O 线程池、顶层异常处理，应优先复用而不是重新实现。若任务需要 ProcessPoolExecutor 或 multiprocessing，则按 `references/windows-packaging.md` 调整模板。不要无条件启用线程、进程、retry 或 timeout；不要保留模板中所有配置项，只保留实际需要的；CPU-bound 任务不要直接套用 `run_concurrently()`。

## 路径与 INI

使用 `sys.frozen` 判断运行环境：`.py` 用 `__file__` 所在目录，PyInstaller EXE 用 `sys.executable` 所在目录。需要读取脚本同级文件时以此目录为基准，不依赖当前工作目录。配置文件默认与脚本同名 `脚本名.ini`，使用 `configparser`，读写明确指定 `encoding="gb2312"`。配置不存在时根据程序需求创建并停止运行，配置项使用中文注释。

## 默认并发与重试

新建批处理工具默认 `THREAD_COUNT = 1`；文件/网络类可重试错误可默认 `RETRY_COUNT = 3`、`RETRY_INTERVAL = 5`，实际任务允许按数据规模、网络环境与错误特征调整。即使模板提供了 `run_concurrently()`，默认也应顺序执行，只有确认 I/O-bound 并发有收益时才启用。

## 依赖与文本

标准库优先。确有需要再引入第三方库并说明用途。txt、csv 等文本默认优先 UTF-8；数据来源明确为旧 Windows 编码时再考虑 GB2312/GBK。不要无条件假设所有文本都是 UTF-8，也不要通过 `errors="ignore"` 静默丢失字符。

## 致命错误与日志

独立 EXE 若适合双击运行，应捕获无法继续的顶层异常，记录明确错误，并根据运行方式决定是否 `input("按 Enter 键退出...")`。不要对所有 Python 程序强制交互暂停；由终端、其他程序或自动化系统调用时应避免阻塞。

新建独立批处理工具默认启用 `logging`。若采用双日志约定，则 `{脚本名}_{时间戳}_info.log` 与 `{脚本名}_{时间戳}_error.log` 分别记录 INFO 与 WARNING/ERROR；日志 `encoding="utf-8"`、`format="%(asctime)s [%(levelname)s] %(message)s"`、时间戳 `YYYYmmdd_HHMMSS`。若环境更适合单一日志文件，则按需调整，不要为双日志增加不必要复杂度。已有脚本存在日志系统时优先沿用，不要为修改一个功能而无理由重写 logging。
