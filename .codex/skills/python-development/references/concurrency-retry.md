# 并发、重试与超时

适用范围：Python 脚本/程序需要处理批量任务、外部程序、网络或耗时操作时，用来判断「是否并发、用线程还是进程、是否重试、是否加超时」。

## 并发选型

先判断任务属于 I/O-bound、CPU-bound、memory-bound、disk-bound、network-bound 还是 external-process-bound。

- I/O-bound：大量文件、网络、UNC、SMB、等待外部 I/O。考虑 `ThreadPoolExecutor`，并发数必须有界，不要无限创建线程。
- CPU-bound：图像计算/转换、压缩、OCR、CPU 密集型 PDF。考虑 `ProcessPoolExecutor` 或 `multiprocessing`。先考虑 CPU 核心数、单任务耗时、任务数、进程创建与 pickle/序列化成本、内存、磁盘竞争、第三方库是否适合多进程。注意每个进程会各自加载一份库与模型，内存峰值约为「进程数 × 单进程占用」。
- 外部程序：调用 ABBYY、Photoshop、Acrobat、ffmpeg、7-Zip、robocopy 等时使用 `subprocess`，并考虑 timeout、returncode、stdout/stderr、有界并发、重试、子进程退出与资源清理。若外部程序自身并行能力强（robocopy `/MT`、7-Zip `-mmt`），不要盲目叠加 Python 并发。
- 小任务：数量少、单任务很短，或并发管理成本超过收益时，顺序执行。

不要因为任务多就自动多线程或多进程。

## 并发失败语义

模板 `run_concurrently()` 的约定（改动前先确认业务真的需要这个语义）：

- 同时在跑的任务数不超过 `thread_count`，未提交的任务留在迭代器里，不会先全部提交；
- `stop_on_error=True`（默认）：任一任务失败后立即停止提交新任务，并取消尚未开始的 future；**已在执行的任务无法中断，会等它结束**；
- 普通异常不向上抛出，收集成失败清单返回；`KeyboardInterrupt` / `SystemExit` 仍然向上传播；
- `stop_on_error=False`：跑完全部任务后再汇总失败。

反面写法：先把全部 future 提交完，再逐个 `future.result()`。那样即使中途出错，剩余任务也会全部执行完毕，既浪费时间，也可能继续对已经出问题的数据动手。

## subprocess 运行时检查

调用外部 EXE 必须检查 `command`、`returncode`、`stdout`、`stderr`、`timeout`。根据程序判断 `returncode != 0` 是否必然失败。不要因为 subprocess 未抛出 Python 异常就认为成功。若可能产生部分输出，失败后判断是否可继续使用、需要验证或应清理。

### robocopy 退出码（最易误判的一处）

robocopy 的退出码是位掩码，**0–7 全部表示成功**，只有 ≥ 8 才是失败：

| 退出码 | 含义 |
| --- | --- |
| 0 | 没有文件需要复制（不是失败） |
| 1 | 有文件复制成功 |
| 2 | 目标中存在源端没有的额外文件 |
| 3 | 1 + 2 |
| 4–7 | 存在不匹配的文件/目录/属性等，仍在成功范围内 |
| ≥ 8 | 真的失败，按位表示各类复制错误 |

所以 `if result.returncode != 0:` 会把「复制成功」判成失败。正确写法：

```python
if result.returncode >= 8:
    raise RuntimeError(f"robocopy 失败，退出码 {result.returncode}")
```

推荐参数：`/R:2 /W:5`（失败重试 2 次、间隔 5 秒，避免默认的百万次重试）、`/NP`（不显示百分比）、`/NJH /NJS`（精简日志）、`/TEE`（同时写控制台与日志文件）、`/XO /XN /XC`（按需跳过旧/新/不同文件）。

## Retry

先区分临时错误与永久错误。

可重试：

- 网络临时断开、UNC 暂时不可访问、某些临时 I/O、外部程序临时启动失败、timeout、服务暂不可用；
- 文件临时被占用：Windows 上表现为 `PermissionError` / `OSError` 且 `exc.winerror == 32`（ERROR_SHARING_VIOLATION，Excel / 杀软仍持有句柄）或 `33`（ERROR_LOCK_VIOLATION）。

不可重试：参数错误、文件格式损坏、数据无效、路径明确不存在、权限不足、不支持的格式、Python 编程错误、明确逻辑错误。

注意 `winerror == 5`（ERROR_ACCESS_DENIED）默认**不重试**：它多数代表持久性权限问题；确实需要重试时由调用点显式加入白名单。模板的 `is_transient()` / `should_retry()` 已按这个白名单实现，不要另写一套判断，否则会出现「文档说可重试、代码却直接判定失败」的矛盾。

原则：临时性失败有限重试，确定性失败尽快失败并报告原因。至少明确最大尝试次数、等待间隔、可重试异常、是否幂等、最终失败如何记录、是否需要 cleanup。必要时 exponential backoff。

默认 `RETRY_COUNT = 3`、`RETRY_INTERVAL = 10`（每次重试固定间隔 10 秒），作为文件/网络类操作的起点，按任务特征调整；两个值都属于可配置项，不要写成散落在代码里的魔法数字。

禁止 `except Exception: pass`，也禁止对所有异常无条件 retry。

## 幂等性

增加 retry 前必须考虑操作失败后是否可能已部分成功。读取、检查、重新生成独立临时输出通常易安全重试。对移动、删除、覆盖、数据库写入、向外部系统提交数据等有副作用的操作，无法确认上次是否成功时，不要简单重复执行，应先检查当前状态，避免重复处理、重复写入、数据丢失、文件损坏。

## Timeout

所有可能无限等待的操作都应判断是否需要 timeout，例如 subprocess、网络请求、外部程序、IPC、可能阻塞的 I/O。timeout 发生后记录日志、尝试安全终止/清理、按错误类型判断是否 retry。不要让单个异常任务无限阻塞整个批处理流程。对无法可靠设置 timeout 的普通本地文件操作，不要为形式而添加没有意义的 timeout。

UNC / 网络路径上的操作更容易卡住（连接半开、SMB 无响应），这类操作必须有 timeout 与有界重试，不要无限等待。
