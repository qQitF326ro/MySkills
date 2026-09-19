# 新建独立脚本脚手架

适用范围：从零编写需要交付为独立程序（常为单文件 EXE）的脚本时，用来建立与 `assets/template.py` 一致的公共结构。

## 模板复用

如果存在 `assets/template.py`，新建前先读取它。若模板已包含路径处理、配置读写、日志、retry、I/O 线程池、顶层异常处理，应优先复用而不是重新实现。若任务需要 ProcessPoolExecutor 或 multiprocessing，则按 `references/windows-packaging.md` 调整模板。不要无条件启用线程、进程、retry 或 timeout；不要保留模板中所有配置项，只保留实际需要的；CPU-bound 任务不要直接套用 `run_concurrently()`。

## 路径与 INI

运行目录判断、INI 配置与编码规则统一见 `references/windows-packaging.md`，本节不再重复。新建脚本时按其中的约定接入模板已有的路径与配置能力。

## 默认并发与重试

新建批处理工具默认 `THREAD_COUNT = 1`（顺序执行），默认启用 `logging`。即使模板提供了 `run_concurrently()`，默认也应顺序执行，只有确认 I/O-bound 并发有收益时才启用。重试属于「确认需要才启用」的能力，默认值与可重试错误类型见 `references/concurrency-retry.md`。

## 依赖与文本

标准库优先。确有需要再引入第三方库并说明用途。txt、csv 等文本与 INI 的编码规则见 `references/windows-packaging.md`，不要在多个文件里各写一套。

## 致命错误与日志

独立 EXE 若适合双击运行，应捕获无法继续的顶层异常，记录明确错误，并根据运行方式决定是否 `input("按 Enter 键退出...")`。不要对所有 Python 程序强制交互暂停；由终端、其他程序或自动化系统调用时应避免阻塞。

新建独立批处理工具默认启用 `logging`，并默认分成两类日志文件：

- 信息日志 `[脚本名]_info_时间戳.log`：只记录 `INFO`。
- 错误日志 `[脚本名]_error_时间戳.log`：记录 `WARNING` 与 `ERROR`。

例如 `example_info_20260919_090400.log`、`example_error_20260919_090400.log`，时间戳格式 `YYYYmmdd_HHMMSS`。

要求：

- `WARNING` 与 `ERROR` 都必须写入错误日志；`WARNING` 不写入信息日志，`ERROR` 也不写入信息日志。
- 日志文件名必须包含脚本/EXE 名称与时间戳：`.py` 用 `__file__` 取名，单文件 `.exe` 用 `sys.executable` 取名（见 `references/windows-packaging.md`）。
- 日志位置默认取脚本/EXE 所在目录，项目明确指定其他位置时遵循项目配置。
- 日志文件明确指定 `encoding="utf-8"`，`format="%(asctime)s [%(levelname)s] %(message)s"`。
- 使用 `logging` 时通过 handler / filter / level 分流，不要在业务代码里手动同时写两个日志文件。
- 日志设计要能承受并发与多线程写入，避免多个线程输出造成日志内容混乱。

若环境更适合单一日志文件，则按需调整，不要为双日志增加不必要复杂度。已有脚本存在日志系统时优先沿用，不要为修改一个功能而无理由重写 logging。
