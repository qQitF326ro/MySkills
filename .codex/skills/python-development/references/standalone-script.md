# 新建独立脚本脚手架

适用范围：从零编写需要交付为独立程序（默认单文件 EXE）的脚本时，用来建立与 `assets/template.py` 一致的公共结构。

## 模板复用

新建前先读取 `assets/template.py`。模板已提供的能力不要重写：

```text
程序所在目录与程序名（.py / EXE 一致）
同名 INI 读写（UTF-8 / UTF-8 BOM / GB18030）
编码回退唯一入口 read_text_auto()
双日志（INFO / WARNING+ERROR），单文件超 10 MB 自动切换新时间戳文件
无控制台环境（pythonw / --noconsole）保护
retry（含 Windows 文件占用 WinError 32/33）
有界线程池（失败即停止提交新任务，返回失败清单）
失败清单 failed_YYYYmmdd_HHMMSS.csv + --only-failed
UNC 替换前备份 / 网络路径不删除 / 临时文件删除边界
资源管理器顺序枚举 sorted_paths()
命令行参数 --force / --yes / --only-failed / --failure-report
退出码 0 / 1 / 2
```

模板只保留实际需要的配置项，删掉不需要的，不要把内部实现细节暴露成用户配置。不要无条件启用线程、进程、retry 或 timeout；CPU-bound 任务不要直接套用 `run_concurrently()`，应改用 `ProcessPoolExecutor` / `multiprocessing`（见 `references/windows-packaging.md`）。

## 路径与 INI

运行目录判断、INI 配置与编码规则统一见 `references/windows-packaging.md`，本节不再重复。新建脚本时按其中约定接入模板已有的路径与配置能力。

## 默认并发与重试

新建批处理工具默认 `THREAD_COUNT = 1`（顺序执行），默认启用 `logging`。即使模板提供了 `run_concurrently()`，默认也应顺序执行，只有确认 I/O-bound 并发有收益时才启用。重试属于「确认需要才启用」的能力，默认值与可重试错误类型见 `references/concurrency-retry.md`。

## 默认日志

默认分成两类日志文件：

- 信息日志 `[脚本名]_info_时间戳.log`：只记录 `INFO`；
- 错误日志 `[脚本名]_error_时间戳.log`：记录 `WARNING` 与 `ERROR`。

要求：

- 时间戳格式 `YYYYmmdd_HHMMSS`，例如 `example_info_20260919_090400.log`；
- `WARNING` 与 `ERROR` 必须写入错误日志，且都不写入信息日志；
- **单个日志文件超过 10 MB 时，切换到新的带时间戳日志文件**，不要截断、不要覆盖；
- 历史日志不做自动清理，需要清理时由用户手动处理（程序不删除自己的历史日志）；
- 日志文件名必须包含脚本/EXE 名称与时间戳：`.py` 用 `__file__` 取名，单文件 `.exe` 用 `sys.executable` 取名（见 `references/windows-packaging.md`）；
- 日志位置默认取脚本/EXE 所在目录，项目明确指定其他位置时遵循项目配置；
- 日志文件明确指定 `encoding="utf-8"`，`format="%(asctime)s [%(levelname)s] %(message)s"`；
- 通过 handler / filter / level 分流，不要在业务代码里手动同时写两个日志文件；
- 日志设计要能承受并发与多线程写入，避免多个线程输出造成日志内容混乱。

若环境更适合单一日志文件，则按需调整，不要为双日志增加不必要复杂度。已有脚本存在日志系统时优先沿用，不要为修改一个功能而无理由重写 logging。

注意：第三方库（PyMuPDF、pikepdf、urllib3 等）的日志走 root logger，模板默认不接管。排障需要时另行配置 root handler，而不是把库日志硬塞进这两个文件。

## 致命错误与退出码

独立 EXE 若适合双击运行，应捕获无法继续的顶层异常，记录明确错误，并根据运行方式决定是否 `input("按 Enter 键退出...")`。不要对所有 Python 程序强制交互暂停；由终端、其他程序或自动化系统调用时应避免阻塞（模板只在 stdin 是终端时才暂停）。

退出码约定见 `references/resume-and-cli.md`：`0` 成功、`1` 致命错误、`2` 部分任务失败。

## 依赖与文本

标准库优先。确有需要再引入第三方库并说明用途。txt、csv 等文本与 INI 的编码规则见 `references/windows-packaging.md`，不要在多个文件里各写一套。

## 验证

交付前按任务风险做验证，并保留可复现的验证手段：

```text
python scripts/selfcheck_template.py    # 模板自身行为回归（50 项冒烟检查）
python scripts/natsort_key.py --self-test   # 排序规则
```

实际脚本的验证方式见 `references/domain-processing.md` 与 `references/pdf-ocr.md`。
