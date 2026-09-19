#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Python 独立脚本模板。

以此为起点改写，用于 Windows 独立脚本、批处理工具以及可打包为单文件 EXE 的程序。

模板提供以下通用能力：
- 直接运行 .py 与 PyInstaller EXE 时，正确定位程序所在目录；
- 默认从程序同目录读取同名 .ini（UTF-8 优先，GB2312 备选）；
- 配置文件不存在时自动创建并停止，避免使用未经用户确认的默认路径；
- 文本读取默认 UTF-8，失败后尝试 GB2312；
- 默认双日志：INFO 与 WARNING/ERROR 分离到两个带时间戳的日志文件；
- 提供仅针对指定异常的 retry 装饰器；
- 提供适用于 I/O-bound 任务的有界线程池辅助函数；
- 顶层异常统一记录，适合命令行和双击运行的独立工具；
- 不默认启用 multiprocessing；CPU-bound 任务应根据实际情况单独设计。

使用原则：
1. 新建脚本前先读取本模板，并按实际需求修改；脚手架约定见
   references/standalone-script.md。
2. 不要无条件启用线程、进程、retry 或 timeout；不要保留模板中所有配置项，
   只保留实际需要的配置。并发/重试/超时与外部程序规则见
   references/concurrency-retry.md。
3. CPU-bound 任务不要直接套用 run_concurrently()，应根据任务特征考虑
   ProcessPoolExecutor / multiprocessing，并遵守 Windows spawn / PyInstaller 规则，
   详见 references/windows-packaging.md。
4. 仅清理当前任务明确创建的临时文件，不根据名称或时间猜测并删除用户文件，
   详见 references/data-integrity.md。
"""

from __future__ import annotations

import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from configparser import ConfigParser
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Callable, Iterable, TypeVar


# ---------------------------------------------------------------------------
# 程序路径与名称
# ---------------------------------------------------------------------------

def get_app_dir() -> Path:
    """返回程序所在目录，兼容直接运行 .py 与 PyInstaller EXE。

    注意：
    - .py：使用 __file__
    - PyInstaller：使用 sys.executable
    - 不依赖当前工作目录 cwd
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_app_name() -> str:
    """返回不含扩展名的程序名，用于同名 INI 和日志文件。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).stem
    return Path(__file__).stem


APP_DIR = get_app_dir()
APP_NAME = get_app_name()
CONFIG_PATH = APP_DIR / f"{APP_NAME}.ini"

CONFIG_SECTION = "CONFIG"
log = logging.getLogger(APP_NAME)


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------
# 这里只提供模板中常用的配置项。
# 最终脚本应删除不需要的配置项，避免把内部实现细节暴露为用户配置。
#
# THREAD_COUNT：
#   仅作为 I/O-bound 任务的线程数示例。
#   默认 1，意味着顺序执行。
#
# RETRY_COUNT / RETRY_INTERVAL：
#   仅作为文件/网络类 transient error 的合理起点：默认 3 次、间隔 10 秒。
#   不代表所有错误都应该 retry。
#
# TIMEOUT：
#   示例默认值，仅在程序存在可能无限等待的操作时使用。
#
# SOURCE_ROOT / TARGET_ROOT：
#   示例路径配置；如果实际程序不需要，应删除。
#
DEFAULT_CONFIG = {
    "THREAD_COUNT": "1",
    "RETRY_COUNT": "3",
    "RETRY_INTERVAL": "10",
    "TIMEOUT": "300",
    "SOURCE_ROOT": "",
    "TARGET_ROOT": "",
}

CONFIG_COMMENTS = {
    "THREAD_COUNT": "I/O 类批处理的并发线程数，默认 1（顺序执行）；仅在确认并发有收益时调大",
    "RETRY_COUNT": "文件/网络类可重试错误的最大尝试次数",
    "RETRY_INTERVAL": "两次尝试之间的基础等待时间（秒）；默认 10 秒",
    "TIMEOUT": "可能长时间等待的操作超时时间（秒）；按实际任务调整",
    "SOURCE_ROOT": "源目录，本地或 UNC 路径；不需要时可删除此配置项",
    "TARGET_ROOT": "目标目录，本地或 UNC 路径；不需要时可删除此配置项",
}


# ---------------------------------------------------------------------------
# 可重试异常
# ---------------------------------------------------------------------------
# 这里只放通用、明显具有“临时性”特征的异常。
#
# 不要把 FileNotFoundError、PermissionError、ValueError 等一般性永久错误
# 加入这里。具体任务如果有明确的 transient exception，可以在业务代码中
# 扩展 exceptions 参数，而不是扩大这里的默认范围。
#
TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def _write_default_config() -> None:
    """创建带中文注释的默认 INI。

    使用 UTF-8，避免在 Windows 与跨平台环境下出现编码不一致。
    """
    lines = [
        "; 自动生成的配置文件，请根据实际任务填写。",
        "; 修改完成后重新运行程序。",
        "",
        f"[{CONFIG_SECTION}]",
        "",
    ]

    for key, value in DEFAULT_CONFIG.items():
        lines.append(f"# {CONFIG_COMMENTS[key]}")
        lines.append(f"{key} = {value}")
        lines.append("")

    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


class ConfigCreatedError(RuntimeError):
    """表示程序首次运行时刚刚创建了配置文件。"""


def load_config() -> ConfigParser:
    """读取同名 INI。

    配置不存在时：
    1. 创建默认 INI；
    2. 抛出 ConfigCreatedError；
    3. 由 main() 停止本次运行。

    这样可以避免程序在用户尚未确认路径等配置时直接执行。
    """
    if not CONFIG_PATH.exists():
        _write_default_config()
        raise ConfigCreatedError(
            f"首次运行已创建配置文件，请填写后重新运行：{CONFIG_PATH}"
        )

    config = ConfigParser(interpolation=None)
    config.read_string(read_text_auto(CONFIG_PATH))

    if not config.has_section(CONFIG_SECTION):
        raise ValueError(
            f"配置文件缺少 [{CONFIG_SECTION}] 节：{CONFIG_PATH}"
        )

    return config


# ---------------------------------------------------------------------------
# 文本读取
# ---------------------------------------------------------------------------

def read_text_auto(path: Path) -> str:
    """按 UTF-8 优先、GB2312 备选读取纯文本文件。

    编码 fallback 只处理 UnicodeDecodeError。
    文件不存在、权限不足等问题直接向上抛出，不应被编码 fallback 掩盖。
    """
    for encoding in ("utf-8", "gb2312"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError(
        f"文件无法按 UTF-8 或 GB2312 解码：{path}"
    )


def read_lines_auto(path: Path) -> list[str]:
    """读取纯文本，逐行去除首尾空白并忽略空行。"""
    return [
        line.strip()
        for line in read_text_auto(path).splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def setup_logging() -> tuple[Path, Path]:
    """初始化双日志。

    输出：
    - {程序名}_info_{时间戳}.log：仅 INFO
    - {程序名}_error_{时间戳}.log：WARNING / ERROR
    - 控制台：INFO 及以上

    每次启动生成新的日志文件，不覆盖历史日志。
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    info_log_path = APP_DIR / f"{APP_NAME}_info_{timestamp}.log"
    error_log_path = APP_DIR / f"{APP_NAME}_error_{timestamp}.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    )

    class InfoOnlyFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return record.levelno == logging.INFO

    log.setLevel(logging.INFO)

    # 允许重复调用 setup_logging() 时安全重建 handler。
    for handler in log.handlers[:]:
        handler.close()
        log.removeHandler(handler)

    info_handler = logging.FileHandler(
        info_log_path,
        encoding="utf-8",
    )
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(InfoOnlyFilter())
    info_handler.setFormatter(formatter)
    log.addHandler(info_handler)

    error_handler = logging.FileHandler(
        error_log_path,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    log.addHandler(error_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(message)s")
    )
    log.addHandler(console_handler)

    return info_log_path, error_log_path


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

def retry(
    times: int = 3,
    interval: float = 10,
    backoff: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = TRANSIENT_EXCEPTIONS,
):
    """仅针对指定异常执行有限次数 retry。

    参数：
    - times：
        总尝试次数，而不是“额外重试次数”。
        例如 times=3 表示最多执行 3 次。
    - interval：
        基础等待时间，单位秒；默认 10 秒。
    - backoff：
        等待时间倍率。
        1.0 = 固定间隔；
        >1.0 = 递增等待。
    - exceptions：
        只有指定异常才会触发 retry。

    注意：
    retry 前必须确认操作具有足够的幂等性，避免重复执行造成副作用。

    不应使用：
        @retry(exceptions=(Exception,))

    来对所有异常无条件重试。
    """
    if times < 1:
        raise ValueError("times 必须 >= 1")
    if interval < 0:
        raise ValueError("interval 不能 < 0")
    if backoff <= 0:
        raise ValueError("backoff 必须 > 0")

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None

            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc

                    if attempt >= times:
                        log.error(
                            "%s 最终失败（第 %d/%d 次）: %s",
                            func.__name__,
                            attempt,
                            times,
                            exc,
                        )
                        raise

                    wait_seconds = interval * (
                        backoff ** (attempt - 1)
                    )

                    log.warning(
                        "%s 失败（第 %d/%d 次），%.2f 秒后重试: %s",
                        func.__name__,
                        attempt,
                        times,
                        wait_seconds,
                        exc,
                    )

                    time.sleep(wait_seconds)

            # 理论上不会到这里；仅用于保证类型与控制流完整。
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# I/O 并发
# ---------------------------------------------------------------------------

T = TypeVar("T")


def run_concurrently(
    items: Iterable[T],
    worker: Callable[[T], object],
    thread_count: int,
) -> None:
    """使用有界线程池并发执行 I/O-bound worker。

    适用于例如：
    - 大量文件读写；
    - 网络 / UNC 文件访问；
    - 等待外部 I/O。

    不适用于直接处理 CPU-bound 任务。

    注意：
    - thread_count=1 时仍然会使用线程池，但行为基本等同顺序提交。
    - 如果任务本身很少或非常轻，推荐直接顺序执行，不必调用本函数。
    - future.result() 会将 worker 中的异常重新抛出。
    - 不在这里自动 retry；retry 应针对具体可重试操作设计。
    """
    thread_count = max(1, int(thread_count))

    items = list(items)
    if not items:
        return

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = [
            executor.submit(worker, item)
            for item in items
        ]

        for future in as_completed(futures):
            future.result()


# ---------------------------------------------------------------------------
# 配置读取辅助函数
# ---------------------------------------------------------------------------

def get_positive_int(
    config: ConfigParser,
    key: str,
    fallback: int,
) -> int:
    """读取必须 >= 1 的整数配置。"""
    value = config.getint(
        CONFIG_SECTION,
        key,
        fallback=fallback,
    )

    if value < 1:
        raise ValueError(
            f"{key} 必须 >= 1，当前值：{value}"
        )

    return value


def get_non_negative_float(
    config: ConfigParser,
    key: str,
    fallback: float,
) -> float:
    """读取必须 >= 0 的浮点配置。"""
    value = config.getfloat(
        CONFIG_SECTION,
        key,
        fallback=fallback,
    )

    if value < 0:
        raise ValueError(
            f"{key} 不能 < 0，当前值：{value}"
        )

    return value


# ---------------------------------------------------------------------------
# 示例业务入口
# ---------------------------------------------------------------------------

def _run() -> None:
    """业务逻辑入口。

    修改本模板时，将这里替换为实际业务逻辑。

    推荐流程：
    1. 读取配置；
    2. 检查输入；
    3. 明确输入 / 输出 / 临时目录；
    4. 根据任务特征判断顺序 / 线程 / 进程；
    5. 执行业务；
    6. 验证结果。
    """
    config = load_config()

    thread_count = get_positive_int(
        config,
        "THREAD_COUNT",
        fallback=1,
    )

    retry_count = get_positive_int(
        config,
        "RETRY_COUNT",
        fallback=3,
    )

    retry_interval = get_non_negative_float(
        config,
        "RETRY_INTERVAL",
        fallback=5,
    )

    timeout = get_non_negative_float(
        config,
        "TIMEOUT",
        fallback=300,
    )

    source_root_raw = config.get(
        CONFIG_SECTION,
        "SOURCE_ROOT",
        fallback="",
    ).strip()

    target_root_raw = config.get(
        CONFIG_SECTION,
        "TARGET_ROOT",
        fallback="",
    ).strip()

    source_root = (
        Path(source_root_raw)
        if source_root_raw
        else None
    )

    target_root = (
        Path(target_root_raw)
        if target_root_raw
        else None
    )

    log.info("APP_DIR=%s", APP_DIR)
    log.info("CONFIG_PATH=%s", CONFIG_PATH)
    log.info(
        "THREAD_COUNT=%s RETRY_COUNT=%s "
        "RETRY_INTERVAL=%s TIMEOUT=%s",
        thread_count,
        retry_count,
        retry_interval,
        timeout,
    )
    log.info(
        "SOURCE_ROOT=%s TARGET_ROOT=%s",
        source_root_raw or "<未配置>",
        target_root_raw or "<未配置>",
    )

    # ------------------------------------------------------------------
    # TODO: 在这里实现实际业务。
    #
    # 新建脚本时：
    #
    # 1. 不需要线程时，直接顺序执行。
    #
    # 2. 确认 I/O-bound 且并发有收益时：
    #
    #       @retry(
    #           times=retry_count,
    #           interval=retry_interval,
    #           exceptions=(ConnectionError, TimeoutError),
    #       )
    #       def process_one(path: Path) -> None:
    #           ...
    #
    #       run_concurrently(
    #           files,
    #           process_one,
    #           thread_count,
    #       )
    #
    # 3. CPU-bound 时不要直接使用上面的线程池。
    #    应根据实际任务考虑 ProcessPoolExecutor / multiprocessing。
    #
    # 4. 使用 multiprocessing / ProcessPoolExecutor 时必须特别检查：
    #
    #       if __name__ == "__main__":
    #           multiprocessing.freeze_support()
    #
    #    以及 Windows spawn、worker 可导入性、进程数量、
    #    PyInstaller 打包后的行为和进程间数据传输成本。
    #
    # 5. 调用 subprocess 时，根据实际任务加入：
    #
    #       timeout
    #       returncode
    #       stdout / stderr
    #       有界并发
    #       必要的 retry
    #
    # 6. 生成最终文件时，根据风险决定是否：
    #
    #       临时文件
    #           ↓
    #       完整写入
    #           ↓
    #       验证
    #           ↓
    #       replace
    #
    # 7. 不要根据文件名、修改时间等猜测并删除临时文件。
    #    只能清理当前任务明确创建的临时文件。
    # ------------------------------------------------------------------

    if source_root is not None and not source_root.exists():
        raise FileNotFoundError(
            f"SOURCE_ROOT 不存在：{source_root}"
        )

    if target_root is not None:
        target_root.mkdir(
            parents=True,
            exist_ok=True,
        )


# ---------------------------------------------------------------------------
# 顶层错误处理
# ---------------------------------------------------------------------------

def pause_before_exit() -> None:
    """适用于双击运行的独立程序。

    仅在可交互终端中尝试暂停。
    被其他程序、自动化工具或非交互环境调用时，不应强制阻塞。
    """
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("按 Enter 键退出...")
    except (EOFError, OSError):
        pass


def main() -> None:
    """程序入口。"""
    try:
        setup_logging()
        _run()

        log.info("程序执行完成。")

    except ConfigCreatedError as exc:
        # 配置首次创建不是业务错误，不使用 ERROR 记录。
        log.info("%s", exc)
        print(f"\n{exc}")
        pause_before_exit()
        sys.exit(0)

    except Exception as exc:
        log.exception("[致命错误] 程序无法继续运行：%s", exc)
        print(f"\n[致命错误] 程序无法继续运行：{exc}")
        pause_before_exit()
        sys.exit(1)


if __name__ == "__main__":
    # 如果本程序未来改用 multiprocessing / ProcessPoolExecutor，
    # 根据 Windows / PyInstaller 要求在这里加入：
    #
    #     import multiprocessing
    #     multiprocessing.freeze_support()
    #
    # 然后再调用 main()。
    main()
