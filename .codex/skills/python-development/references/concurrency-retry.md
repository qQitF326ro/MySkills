# 并发、重试与超时

适用范围：Python 脚本/程序需要处理批量任务、外部程序、网络或耗时操作时，用来判断「是否并发、用线程还是进程、是否重试、是否加超时」。

## 并发选型

先判断任务属于 I/O-bound、CPU-bound、memory-bound、disk-bound、network-bound 还是 external-process-bound。

- I/O-bound：大量文件、网络、UNC、SMB、等待外部 I/O。考虑 `ThreadPoolExecutor`，并发数必须有界，不要无限创建线程。
- CPU-bound：图像计算/转换、压缩、OCR 后处理、CPU 密集型 PDF。考虑 `ProcessPoolExecutor` 或 `multiprocessing`。先考虑 CPU 核心数、单任务耗时、任务数、进程创建与 pickle/序列化成本、内存、磁盘竞争、第三方库是否适合多进程。
- 外部程序：调用 ABBYY、Photoshop、Acrobat、ffmpeg、7-Zip、robocopy 等时使用 `subprocess`，并考虑 timeout、returncode、stdout/stderr、有界并发、重试、子进程退出与资源清理。若外部程序自身并行能力强，不要盲目叠加 Python 并发。
- 小任务：数量少、单任务很短，或并发管理成本超过收益时，顺序执行。

不要因为任务多就自动多线程或多进程。

## subprocess 运行时检查

调用外部 EXE 必须检查 `command`、`returncode`、`stdout`、`stderr`、`timeout`。根据程序判断 `returncode != 0` 是否必然失败。不要因为 subprocess 未抛出 Python 异常就认为成功。若可能产生部分输出，失败后判断是否可继续使用、需要验证或应清理。

## Retry

先区分临时错误与永久错误。

可重试：网络临时断开、UNC 暂时不可访问、文件临时被占用、某些临时 I/O、外部程序临时启动失败、timeout、服务暂不可用。

不可重试：参数错误、文件格式损坏、数据无效、路径明确不存在、权限不足、不支持的格式、Python 编程错误、明确逻辑错误。

原则：临时性失败有限重试，确定性失败尽快失败并报告原因。至少明确最大尝试次数、等待间隔、可重试异常、是否幂等、最终失败如何记录、是否需要 cleanup。必要时 exponential backoff。

默认 `RETRY_COUNT = 3`、`RETRY_INTERVAL = 10`（每次重试固定间隔 10 秒），作为文件/网络类操作的起点，按任务特征调整；两个值都属于可配置项，不要写成散落在代码里的魔法数字。

禁止 `except Exception: pass`，也禁止对所有异常无条件 retry。

## 幂等性

增加 retry 前必须考虑操作失败后是否可能已部分成功。读取、检查、重新生成独立临时输出通常易安全重试。对移动、删除、覆盖、数据库写入、向外部系统提交数据等有副作用的操作，无法确认上次是否成功时，不要简单重复执行，应先检查当前状态，避免重复处理、重复写入、数据丢失、文件损坏。

## Timeout

所有可能无限等待的操作都应判断是否需要 timeout，例如 subprocess、网络请求、外部程序、IPC、可能阻塞的 I/O。timeout 发生后记录日志、尝试安全终止/清理、按错误类型判断是否 retry。不要让单个异常任务无限阻塞整个批处理流程。对无法可靠设置 timeout 的普通本地文件操作，不要为形式而添加没有意义的 timeout。
