#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Python 独立脚本模板。

以此为起点改写，用于 Windows 独立脚本、批处理工具以及可打包为单文件 EXE 的程序。

模板提供以下通用能力：
- 直接运行 .py 与 PyInstaller EXE 时，正确定位程序所在目录；
- 默认从程序同目录读取同名 .ini（UTF-8 / UTF-8 BOM / GB18030 自动识别）；
- 配置文件不存在时自动创建并停止，避免使用未经用户确认的默认路径；
- 编码回退集中在 read_text_auto() 一处，其他位置不要各写一套；
- 默认双日志：INFO 与 WARNING/ERROR 分离；单个日志文件超过 10 MB 时自动
  切换到新的带时间戳文件（历史日志保留，不自动清理）；
- 仅针对可重试异常的 retry 装饰器（含 Windows 文件占用 WinError 32/33）；
- 适用于 I/O-bound 任务的有界线程池：失败即停止提交新任务并汇总失败清单；
- 失败清单 failed_YYYYmmdd_HHMMSS.csv（原路径 / 失败阶段 / 异常摘要 / 是否可重试），
  可配合 --only-failed 做二次运行；
- UNC / 网络路径下替换文件前自动备份到程序目录，并保留相对路径；
- 文件名排序与 Windows 资源管理器一致（数字段按数值比较）；
- 命令行参数：--force / --yes（--no-confirm）/ --only-failed / --failure-report；
- 退出码约定：0 成功、1 致命错误、2 部分任务失败；
- 顶层异常统一记录，适合命令行和双击运行的独立工具；
- 不默认启用 multiprocessing；CPU-bound 任务应根据实际情况单独设计。

使用原则：
1. 新建脚本前先读取本模板，并按实际需求修改；脚手架约定见
   references/standalone-script.md。
2. 不要无条件启用线程、进程、retry 或 timeout；不要保留模板中所有配置项，
   只保留实际需要的配置。并发、重试、超时与外部程序规则见
   references/concurrency-retry.md。
3. CPU-bound 任务不要直接套用 run_concurrently()，应根据任务特征考虑
   ProcessPoolExecutor / multiprocessing，并遵守 Windows spawn / PyInstaller 规则，
   详见 references/windows-packaging.md。
4. 打包 EXE 默认单文件；只有用户明确要求单目录时才使用 --onedir，
   详见 references/pyinstaller.md。
5. 删除与替换的安全边界见 references/data-integrity.md：只能清理当前任务
   自己创建的临时文件；UNC / 网络路径默认不删除文件，替换前先备份。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import re
import shutil
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 单个日志文件的最大字节数，超过后切换到新的带时间戳日志文件。
LOG_MAX_BYTES = 10 * 1024 * 1024

# 文本编码回退顺序。utf-8-sig 同时兼容带 BOM 与不带 BOM 的 UTF-8；
# gb18030 是 GB2312 / GBK 的超集，用于旧 Windows 中文本地文件。
TEXT_ENCODINGS = ("utf-8-sig", "gb18030")

# 被替换的目标文件备份到该子目录（相对程序所在目录）。
BACKUP_SUBDIR = "backup"

# 失败清单文件名前缀与列名。
FAILURE_REPORT_PREFIX = "failed_"
FAILURE_REPORT_COLUMNS = ("原路径", "失败阶段", "异常摘要", "是否可重试")

# 退出码约定。
EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PARTIAL_FAILURE = 2


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
    """返回不含扩展名的程序名，用于同名 INI、日志和失败清单。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).stem
    return Path(__file__).stem


APP_DIR = get_app_dir()
APP_NAME = get_app_name()
CONFIG_PATH = APP_DIR / f"{APP_NAME}.ini"

CONFIG_SECTION = "CONFIG"

# logger 名里不能出现点号，否则会形成 logger 层级；文件名含 "v1.0" 这类
# 版本号时尤其容易出现，这里统一替换掉。
log = logging.getLogger(APP_NAME.replace(".", "_"))


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------

def is_network_path(path: Path | str) -> bool:
    """判断是否为 UNC / 网络路径（\\\\server\\share 或 //server/share）。

    排除以下两类「看起来像 UNC 但其实不是」的前缀：
    - \\\\?\\ 长路径前缀（仍是本地路径）；
    - \\\\.\\ 设备命名空间。
    """
    return bool(re.match(r"^[\\/]{2}(?![\\/?.])", str(path)))


def _load_strcmp_logical():
    """加载 shlwapi.StrCmpLogicalW，失败时返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        shlwapi = ctypes.WinDLL("shlwapi")
        function = shlwapi.StrCmpLogicalW
        function.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        function.restype = ctypes.c_int
        return function
    except (AttributeError, OSError):
        return None


_STRCMP_LOGICAL = _load_strcmp_logical()


class _NaturalKey:
    """纯 Python 自然排序 key，作为 StrCmpLogicalW 不可用时的等价回退。

    数字段按数值比较，其余部分不区分大小写；数值相同（p1 与 p01）时用
    原字符串做字典序 tiebreak，与 _ExplorerKey 的规则保持一致，
    保证两种实现顺序完全一致、且结果稳定。
    """

    __slots__ = ("name", "parts")

    def __init__(self, name: str) -> None:
        self.name = name
        parts = []
        for part in re.split(r"(\d+)", name):
            if not part:
                continue
            if part.isdigit():
                parts.append((0, int(part)))
            else:
                parts.append((1, part.casefold()))
        self.parts = tuple(parts)

    def __lt__(self, other: "_NaturalKey") -> bool:
        if not isinstance(other, _NaturalKey):
            return NotImplemented
        if self.parts != other.parts:
            return self.parts < other.parts
        return self.name < other.name

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _NaturalKey):
            return NotImplemented
        return self.parts == other.parts and self.name == other.name


class _ExplorerKey:
    """按资源管理器规则比较文件名的排序 key。"""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __lt__(self, other: "_ExplorerKey") -> bool:
        if not isinstance(other, _ExplorerKey):
            return NotImplemented
        result = _STRCMP_LOGICAL(self.name, other.name)
        if result == 0:
            # 资源管理器认为 "01" 与 "1" 等价，这里用原字符串保证顺序确定。
            return self.name < other.name
        return result < 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _ExplorerKey):
            return NotImplemented
        return _STRCMP_LOGICAL(self.name, other.name) == 0


def explorer_sort_key(path: Path | str, *, by_name: bool = True):
    """返回与 Windows 资源管理器一致的排序 key。

    资源管理器使用 shlwapi.StrCmpLogicalW：数字段按数值比较（img10 排在
    img2 之后），其余部分不区分大小写。非 Windows 或 API 不可用时退回
    等价的纯 Python 自然排序。规范说明见 references/windows-packaging.md。
    """
    text = str(path)
    if by_name:
        text = Path(text).name
    if _STRCMP_LOGICAL is not None:
        return _ExplorerKey(text)
    return _NaturalKey(text)


def sorted_paths(paths: Iterable[Path | str], *, by_name: bool = True) -> list[Path]:
    """按资源管理器顺序排序路径，默认比较文件名。

    批量处理必须显式排序，不要依赖 os.listdir / iterdir 的返回顺序；
    也不要使用 sorted() 的默认字典序（会把 img10 排在 img2 之前）。
    """
    return sorted(
        (Path(p) for p in paths),
        key=lambda item: explorer_sort_key(item, by_name=by_name),
    )


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
#   仅作为文件/网络类 transient error 的合理起点：默认 3 次、固定间隔 10 秒。
#   不代表所有错误都应该 retry。
#
# TIMEOUT：
#   示例默认值，仅在程序存在可能无限等待的操作时使用。
#
# ALLOW_DIRECT_OVERWRITE：
#   UNC / 网络路径下替换文件时，是否跳过「先备份再替换」。
#   默认 0（必须先备份）；只有用户明确要求直接覆盖时才设为 1。
#
# SOURCE_ROOT / TARGET_ROOT：
#   示例路径配置；如果实际程序不需要，应删除。
#
DEFAULT_CONFIG = {
    "THREAD_COUNT": "1",
    "RETRY_COUNT": "3",
    "RETRY_INTERVAL": "10",
    "TIMEOUT": "300",
    "ALLOW_DIRECT_OVERWRITE": "0",
    "SOURCE_ROOT": "",
    "TARGET_ROOT": "",
}

CONFIG_COMMENTS = {
    "THREAD_COUNT": "I/O 类批处理的并发线程数，默认 1（顺序执行）；仅在确认并发有收益时调大",
    "RETRY_COUNT": "文件/网络类可重试错误的最大尝试次数",
    "RETRY_INTERVAL": "两次尝试之间的基础等待时间（秒）；默认 10 秒",
    "TIMEOUT": "可能长时间等待的操作超时时间（秒）；按实际任务调整",
    "ALLOW_DIRECT_OVERWRITE": "UNC/网络路径替换时是否跳过备份直接覆盖：0=必须先备份（默认），1=不备份（仅在用户明确要求时使用）",
    "SOURCE_ROOT": "源目录，本地或 UNC 路径；不需要时可删除此配置项",
    "TARGET_ROOT": "目标目录，本地或 UNC 路径；不需要时可删除此配置项",
}


# ---------------------------------------------------------------------------
# 配置文件
# ---------------------------------------------------------------------------

def _write_default_config() -> None:
    """创建带中文注释的默认 INI。

    使用 UTF-8 且不带 BOM，避免 Windows 记事本另存为时引入 BOM 分歧。
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
    读取统一走 read_text_auto()，带 BOM 的 UTF-8 与 GB18030 都能正常解析。
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
# 文本读取：全脚本唯一的编码回退入口
# ---------------------------------------------------------------------------

def read_text_auto(path: Path) -> str:
    """按 UTF-8（含 BOM）、GB18030 顺序读取纯文本。

    这是全脚本唯一的编码回退实现，INI、txt、csv、清单文件都走这里，
    其他位置不要再各写一套。

    - utf-8-sig 同时兼容带 BOM 与不带 BOM 的 UTF-8。带 BOM 的文件若按
      utf-8 读取，首字符会残留 \\ufeff，configparser 会直接抛
      MissingSectionHeaderError。
    - gb18030 是 GB2312 / GBK 的超集，能覆盖「镕」等 GB2312 之外的常用字；
      只用 gb2312 会在旧中文本地文件上误报解码失败。
    - 回退只处理 UnicodeDecodeError；文件不存在、权限不足直接向上抛出，
      不被编码 fallback 掩盖。
    - 不使用 errors="ignore"，避免静默丢数据。

    大文件不要用本函数整体读入内存，改用 open(..., encoding=...) 流式读取，
    编码判断仍可复用 TEXT_ENCODINGS。
    """
    for encoding in TEXT_ENCODINGS:
        try:
            return Path(path).read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise ValueError(
        f"文件无法按 {' 或 '.join(TEXT_ENCODINGS)} 解码：{path}"
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

class InfoOnlyFilter(logging.Filter):
    """只放行 INFO 级别，保证 WARNING/ERROR 不写入信息日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == logging.INFO


class SizeRollingTimestampHandler(logging.Handler):
    """写入 {程序名}_{类别}_{时间戳}.log，单文件超过 max_bytes 时切换新文件。

    - 每次启动使用新的时间戳文件，不覆盖历史日志；
    - 运行过程中当前文件超过 max_bytes 时，自动切换到新的时间戳文件，
      而不是截断或覆盖；
    - 历史日志不做自动清理，需要清理时由用户手动处理；
    - 单条记录本身超过 max_bytes 时仍完整写入，不会陷入反复切换。
    """

    terminator = "\n"

    def __init__(
        self,
        directory: Path,
        app_name: str,
        tag: str,
        max_bytes: int = LOG_MAX_BYTES,
        encoding: str = "utf-8",
    ) -> None:
        super().__init__()
        self._directory = Path(directory)
        self._app_name = app_name
        self._tag = tag
        self._max_bytes = int(max_bytes)
        self._encoding = encoding
        self._stream = None
        self._size = 0
        self._used: set = set()
        self.paths: list[Path] = []
        self._open_new_file()

    @property
    def current_path(self) -> Path:
        return self.paths[-1]

    def _next_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = self._directory / f"{self._app_name}_{self._tag}_{stamp}.log"
        candidate = base
        index = 1
        while candidate in self._used or candidate.exists():
            candidate = base.with_name(f"{base.stem}_{index}{base.suffix}")
            index += 1
        return candidate

    def _open_new_file(self) -> None:
        path = self._next_path()
        self._stream = path.open("a", encoding=self._encoding, newline="")
        self._size = path.stat().st_size
        self._used.add(path)
        self.paths.append(path)

    def _close_stream(self) -> None:
        stream = self._stream
        if stream is not None:
            try:
                stream.flush()
                stream.close()
            finally:
                self._stream = None

    def _roll_over(self) -> None:
        self._close_stream()
        self._open_new_file()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record) + self.terminator
            payload = len(message.encode(self._encoding))

            if self._stream is None:
                self._open_new_file()
            elif (
                self._max_bytes > 0
                and self._size > 0
                and self._size + payload > self._max_bytes
            ):
                self._roll_over()

            stream = self._stream
            if stream is None:
                return

            stream.write(message)
            stream.flush()
            self._size += payload
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            self._close_stream()
        finally:
            super().close()

    def all_paths(self) -> list[Path]:
        """返回本次运行写过的全部日志文件（含切换后的新文件）。"""
        return list(self.paths)


def get_console_stream():
    """返回可用的控制台输出流；pythonw / --noconsole 下返回 None。

    - sys.stdout 为 None 时直接跳过控制台输出。否则 logging.StreamHandler
      会回退到 sys.stderr（它同样可能是 None），写入失败会触发 logging
      内部错误处理，表现为静默丢失或偶发崩溃。
    - Windows 中文环境控制台编码通常是 GBK / cp936，日志中的 ✓、emoji、
      生僻字会触发 UnicodeEncodeError，这里统一改为 UTF-8 + errors="replace"。
    """
    stream = sys.stdout
    if stream is None:
        return None
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    return stream


def setup_logging() -> tuple[Path, Path]:
    """初始化双日志。

    输出：
    - {程序名}_info_{时间戳}.log：仅 INFO
    - {程序名}_error_{时间戳}.log：WARNING / ERROR
    - 控制台：INFO 及以上（无可用控制台时自动跳过）

    单个日志文件超过 LOG_MAX_BYTES（10 MB）时切换到新的时间戳文件。
    第三方库（PyMuPDF、pikepdf、urllib3 等）的日志走 root logger，
    本函数不接管；需要收集时另行配置 root handler。

    返回本次启动使用的初始 (信息日志, 错误日志) 路径。
    """
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    )

    log.setLevel(logging.INFO)
    log.propagate = False

    # 允许重复调用 setup_logging() 时安全重建 handler。
    for handler in log.handlers[:]:
        handler.close()
        log.removeHandler(handler)

    info_handler = SizeRollingTimestampHandler(APP_DIR, APP_NAME, "info")
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(InfoOnlyFilter())
    info_handler.setFormatter(formatter)
    log.addHandler(info_handler)

    error_handler = SizeRollingTimestampHandler(APP_DIR, APP_NAME, "error")
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)
    log.addHandler(error_handler)

    stream = get_console_stream()
    if stream is not None:
        console_handler = logging.StreamHandler(stream)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            logging.Formatter("[%(levelname)s] %(message)s")
        )
        log.addHandler(console_handler)

    return info_handler.current_path, error_handler.current_path


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

# 通用临时性异常。
TRANSIENT_EXCEPTIONS: tuple = (ConnectionError, TimeoutError)

# Windows 文件被占用类错误：
#   32 ERROR_SHARING_VIOLATION（其他进程持有句柄，如 Excel 未关闭）
#   33 ERROR_LOCK_VIOLATION（文件区域被锁定）
# 不包含 5 ERROR_ACCESS_DENIED：它更可能是持久性权限问题，
# 需要重试时由调用点显式加入，不要放进默认白名单。
TRANSIENT_WINERRORS = frozenset({32, 33})


def is_transient(exc: BaseException) -> bool:
    """判断异常是否属于「可能临时、值得重试」的类别。

    与 references/concurrency-retry.md 中的重试白名单保持一致：
    临时网络故障、超时、Windows 文件被占用可重试；
    参数错误、格式损坏、路径不存在、权限不足、编程错误不重试。
    """
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in TRANSIENT_WINERRORS:
        return True
    return False


def should_retry(
    exc: BaseException,
    exceptions: tuple = TRANSIENT_EXCEPTIONS,
) -> bool:
    """默认重试判定：显式列出的异常类型，或 Windows 文件占用类错误。"""
    if isinstance(exc, exceptions):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in TRANSIENT_WINERRORS:
        return True
    return False


def retry(
    times: int = 3,
    interval: float = 10,
    backoff: float = 1.0,
    exceptions: tuple = TRANSIENT_EXCEPTIONS,
    retry_if: Callable[[BaseException], bool] | None = None,
):
    """仅针对可重试异常执行有限次数 retry。

    参数：
    - times：
        总尝试次数，而不是「额外重试次数」。
        例如 times=3 表示最多执行 3 次。
    - interval：
        基础等待时间，单位秒；默认 10 秒。
    - backoff：
        等待时间倍率。
        1.0 = 固定间隔；
        >1.0 = 递增等待。
    - exceptions：
        额外视为可重试的异常类型，默认 TRANSIENT_EXCEPTIONS。
        即使不传，Windows 文件被占用（WinError 32/33）也会重试。
    - retry_if：
        自定义判定函数，传入时完全接管判定逻辑。

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
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    retryable = (
                        retry_if(exc)
                        if retry_if is not None
                        else should_retry(exc, exceptions)
                    )

                    # 不可重试的确定性错误直接失败，由调用方决定如何处理。
                    if not retryable:
                        raise

                    if attempt >= times:
                        log.error(
                            "%s 最终失败（第 %d/%d 次）: %s",
                            func.__name__,
                            attempt,
                            times,
                            exc,
                        )
                        raise

                    wait_seconds = interval * (backoff ** (attempt - 1))

                    log.warning(
                        "%s 失败（第 %d/%d 次），%.2f 秒后重试: %s",
                        func.__name__,
                        attempt,
                        times,
                        wait_seconds,
                        exc,
                    )

                    time.sleep(wait_seconds)

            # 循环内要么 return 要么 raise，不会到达这里；保留用于类型完整。
            raise RuntimeError(f"{func.__name__} retry 流程异常终止")

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 失败清单
# ---------------------------------------------------------------------------

@dataclass
class FailureRecord:
    """单个失败任务：原路径、失败阶段、异常摘要、是否可重试。"""

    source: str
    stage: str
    error: str
    retryable: bool


def failure_record(
    item: Any,
    error: BaseException,
    stage: str,
) -> FailureRecord:
    """由任务对象与异常生成失败记录。"""
    if isinstance(item, (str, Path)):
        source = str(item)
    else:
        source = repr(item)
    return FailureRecord(
        source=source,
        stage=stage,
        error=f"{type(error).__name__}: {error}",
        retryable=is_transient(error),
    )


def write_failure_report(
    records: Iterable[FailureRecord],
    directory: Path | None = None,
) -> Path | None:
    """写出 failed_YYYYmmdd_HHMMSS.csv；没有失败时不产生文件。

    - 使用 utf-8-sig，Excel 双击打开不乱码；
    - 写在程序所在目录，便于二次运行与人工核对；
    - 只记录失败项，不写「成功但需要人工确认」的中间状态。
    """
    items = list(records)
    if not items:
        return None

    target_dir = Path(directory) if directory is not None else APP_DIR
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = target_dir / f"{FAILURE_REPORT_PREFIX}{stamp}.csv"

    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(FAILURE_REPORT_COLUMNS)
        for record in items:
            writer.writerow(
                [
                    record.source,
                    record.stage,
                    record.error,
                    "是" if record.retryable else "否",
                ]
            )

    return path


def latest_failure_report(directory: Path | None = None) -> Path | None:
    """返回目录下最新的失败清单（按时间戳文件名排序）。"""
    target_dir = Path(directory) if directory is not None else APP_DIR
    candidates = sorted(target_dir.glob(f"{FAILURE_REPORT_PREFIX}*.csv"))
    return candidates[-1] if candidates else None


def load_failure_report(
    path: Path,
    column: str = "原路径",
) -> list[str]:
    """读取失败清单中指定列，供 --only-failed 二次运行使用。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"失败清单不存在：{path}")

    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        fieldnames = reader.fieldnames or []
        if column not in fieldnames:
            raise ValueError(
                f"失败清单缺少「{column}」列：{path}（实际列：{fieldnames}）"
            )
        return [
            (row.get(column) or "").strip()
            for row in reader
            if (row.get(column) or "").strip()
        ]


def select_items_by_failure_report(args) -> list[str]:
    """按 --only-failed 选择待重跑的原路径；未启用时返回空列表。"""
    if not getattr(args, "only_failed", False):
        return []

    report = args.failure_report or latest_failure_report()
    if report is None:
        raise FileNotFoundError(
            f"未找到失败清单，无法使用 --only-failed：{APP_DIR}"
        )

    sources = load_failure_report(report)
    log.info("--only-failed：从 %s 读取 %d 条待重跑记录", report, len(sources))
    return sources


# ---------------------------------------------------------------------------
# 并发
# ---------------------------------------------------------------------------

T = TypeVar("T")


def run_concurrently(
    items: Iterable[T],
    worker: Callable[[T], object],
    thread_count: int,
    *,
    stop_on_error: bool = True,
    stage: str = "处理",
) -> list[FailureRecord]:
    """使用有界线程池并发执行 I/O-bound worker，返回失败清单。

    适用于例如：
    - 大量文件读写；
    - 网络 / UNC 文件访问；
    - 等待外部 I/O。

    不适用于 CPU-bound 任务（改用 ProcessPoolExecutor / multiprocessing）。

    执行语义（有意这样设计，避免「任务先全部提交、出错也要跑完」的隐式行为）：
    - 同时在跑的任务数不超过 thread_count，未提交的任务只留在迭代器里；
    - stop_on_error 为真（默认）时，一旦有任务失败就不再提交新任务，
      并取消尚未开始的 future；已经在执行的任务无法中断，会等它结束；
    - worker 抛出的普通异常不向上传播，而是收集成 FailureRecord 返回，
      避免单个坏文件中断整个批处理；需要「失败即整体中止」时请根据返回的
      失败清单自行判断；
    - KeyboardInterrupt / SystemExit 等 BaseException 仍会向上传播。
    """
    thread_count = max(1, int(thread_count))
    failures: list[FailureRecord] = []
    iterator = iter(items)
    stopping = False

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures: dict = {}

        def submit_next() -> bool:
            try:
                item = next(iterator)
            except StopIteration:
                return False
            futures[executor.submit(worker, item)] = item
            return True

        for _ in range(thread_count):
            if not submit_next():
                break

        while futures:
            done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)

            for future in done:
                item = futures.pop(future, None)
                if future.cancelled():
                    continue

                error = future.exception()
                if error is None:
                    if not stopping:
                        submit_next()
                    continue

                if not isinstance(error, Exception):
                    raise error

                record = failure_record(item, error, stage)
                failures.append(record)
                log.error(
                    "%s 失败：%s（%s）",
                    stage,
                    record.source,
                    record.error,
                )

                if stop_on_error:
                    stopping = True
                    for pending in futures:
                        pending.cancel()
                else:
                    # 继续跑完剩余任务，失败项记入清单后由调用方统一处理。
                    submit_next()

    return failures


# ---------------------------------------------------------------------------
# 文件替换与临时文件安全
# ---------------------------------------------------------------------------

def backup_file(
    path: Path,
    source_root: Path,
    backup_root: Path,
) -> Path:
    """把即将被替换的文件备份到 backup_root，并保留相对 source_root 的路径。

    对应规则（见 references/data-integrity.md）：
    UNC / 网络路径没有回收站，替换前必须先备份；备份目录默认是程序所在
    目录下的 backup/，这样备份落在本地磁盘，不受网络抖动影响。
    """
    path = Path(path)
    source_root = Path(source_root)
    backup_root = Path(backup_root)

    try:
        relative = path.resolve().relative_to(source_root.resolve())
    except ValueError:
        # 不在 source_root 之下（例如跨盘、跨共享）时用稳定哈希做扁平化，
        # 不写 ".." 这类会逃出备份目录的相对路径。
        digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
        relative = Path("_external") / f"{digest}_{path.name}"

    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return destination


def atomic_replace(
    target: Path,
    temp_path: Path,
    *,
    verify: Callable[[Path], None] | None = None,
    source_root: Path | None = None,
    backup_root: Path | None = None,
    allow_direct_overwrite: bool = False,
) -> Path | None:
    """用已写好的临时文件替换目标文件，返回备份路径（未备份时为 None）。

    流程：verify(临时文件) → 必要时备份目标 → temp.replace(target)。

    - verify 抛异常时目标文件保持原样；
    - UNC / 网络路径：Path.replace() 在 SMB 上并非原子操作，默认先备份
      目标文件（保留相对路径）再替换；只有用户明确要求直接覆盖时才传
      allow_direct_overwrite=True 跳过备份；
    - 本地路径沿用「临时文件 + 完整写入 + 验证 + 替换」流程，不额外备份。
    """
    target = Path(target)
    temp_path = Path(temp_path)

    if verify is not None:
        verify(temp_path)

    backup_path: Path | None = None
    if (
        target.exists()
        and is_network_path(target)
        and not allow_direct_overwrite
    ):
        backup_path = backup_file(
            target,
            Path(source_root) if source_root is not None else target.parent,
            Path(backup_root) if backup_root is not None else APP_DIR / BACKUP_SUBDIR,
        )

    temp_path.replace(target)
    return backup_path


def remove_own_temp_file(path: Path, temp_root: Path) -> bool:
    """删除当前任务自己创建的临时文件，返回是否真的删除。

    双重保护：
    - 只允许删除 temp_root 之下的文件（本次任务创建的临时目录）；
    - UNC / 网络路径一律不删除，直接返回 False 并保留文件。

    绝不根据文件名、修改时间、扩展名或大小猜测要删除的文件。
    """
    path = Path(path)
    temp_root = Path(temp_root)

    if is_network_path(path):
        log.warning("网络路径下不执行删除，已保留：%s", path)
        return False

    try:
        resolved = path.resolve()
        root = temp_root.resolve()
    except OSError:
        return False

    # 用规范化绝对路径与目录边界判断归属，不做字符串前缀匹配。
    if resolved == root or root not in resolved.parents:
        return False

    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


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
        raise ValueError(f"{key} 必须 >= 1，当前值：{value}")

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
        raise ValueError(f"{key} 不能 < 0，当前值：{value}")

    return value


def get_bool(
    config: ConfigParser,
    key: str,
    fallback: bool = False,
) -> bool:
    """读取布尔配置，接受 1/0、true/false、yes/no、on/off、是/否。"""
    raw = config.get(CONFIG_SECTION, key, fallback=None)
    if raw is None or str(raw).strip() == "":
        return fallback
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y", "是"}


# ---------------------------------------------------------------------------
# 交互确认与命令行参数
# ---------------------------------------------------------------------------

def confirm(prompt: str, assume_yes: bool = False) -> bool:
    """运行前确认。

    - assume_yes（--yes / --no-confirm）直接返回 True，供自动化调用；
    - 非交互环境（无 tty）不阻塞，记录日志后继续；
    - 交互环境下等待输入 Y/N。
    """
    if assume_yes:
        return True

    if sys.stdin is None or not sys.stdin.isatty():
        log.info("非交互环境，跳过运行前确认：%s", prompt)
        return True

    print(f"{prompt} [Y/N] ", end="")
    try:
        answer = input().strip().lower()
    except (EOFError, OSError):
        return True
    return answer in {"y", "yes", "是"}


def build_arg_parser() -> argparse.ArgumentParser:
    """构造标准命令行参数。

    语义与 references/resume-and-cli.md 保持一致：
    - --force 管处理范围（忽略已完成判定，重新生成全部目标）；
    - --yes / --no-confirm 管是否交互；
    - --only-failed 只重跑最近一次失败清单中的文件。
    """
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} 命令行参数",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略已完成判定与断点状态，重新处理全部目标",
    )
    parser.add_argument(
        "--yes",
        "--no-confirm",
        dest="assume_yes",
        action="store_true",
        help="跳过运行前交互确认，直接开始（自动化调用使用）",
    )
    parser.add_argument(
        "--only-failed",
        action="store_true",
        help="仅处理失败清单中的文件（默认取最新的 failed_*.csv）",
    )
    parser.add_argument(
        "--failure-report",
        type=Path,
        default=None,
        help="指定失败清单 CSV；配合 --only-failed 使用",
    )
    return parser


# ---------------------------------------------------------------------------
# 业务入口
# ---------------------------------------------------------------------------

def _run(args) -> int:
    """业务逻辑入口，返回退出码。

    修改本模板时，把输入枚举与 process_one() 替换为实际业务逻辑。

    推荐流程：
    1. 读取配置；
    2. 明确输入 / 输出 / 临时目录，并检查输入是否存在；
    3. 用 sorted_paths() 枚举输入，保证顺序与资源管理器一致；
    4. 根据任务特征判断顺序 / 线程 / 进程；
    5. 执行业务；
    6. 验证结果，写出失败清单。
    """
    config = load_config()

    thread_count = get_positive_int(config, "THREAD_COUNT", fallback=1)
    retry_count = get_positive_int(config, "RETRY_COUNT", fallback=3)
    retry_interval = get_non_negative_float(config, "RETRY_INTERVAL", fallback=10)
    timeout = get_non_negative_float(config, "TIMEOUT", fallback=300)
    allow_direct_overwrite = get_bool(
        config,
        "ALLOW_DIRECT_OVERWRITE",
        fallback=False,
    )

    source_root_raw = config.get(CONFIG_SECTION, "SOURCE_ROOT", fallback="").strip()
    target_root_raw = config.get(CONFIG_SECTION, "TARGET_ROOT", fallback="").strip()

    source_root = Path(source_root_raw) if source_root_raw else None
    target_root = Path(target_root_raw) if target_root_raw else None

    log.info("APP_DIR=%s", APP_DIR)
    log.info("CONFIG_PATH=%s", CONFIG_PATH)
    log.info(
        "THREAD_COUNT=%s RETRY_COUNT=%s RETRY_INTERVAL=%s TIMEOUT=%s",
        thread_count,
        retry_count,
        retry_interval,
        timeout,
    )
    log.info(
        "SOURCE_ROOT=%s TARGET_ROOT=%s ALLOW_DIRECT_OVERWRITE=%s",
        source_root_raw or "<未配置>",
        target_root_raw or "<未配置>",
        allow_direct_overwrite,
    )
    log.info("--force=%s --only-failed=%s", args.force, args.only_failed)

    # 输入选择。
    if args.only_failed:
        items = [Path(item) for item in select_items_by_failure_report(args)]
    else:
        if source_root is None:
            raise ValueError(
                "SOURCE_ROOT 未配置：请在 INI 中填写源目录，"
                "或使用 --only-failed 只重跑失败清单"
            )
        if not source_root.exists():
            raise FileNotFoundError(f"SOURCE_ROOT 不存在：{source_root}")
        # 枚举顺序必须显式确定，不要依赖 iterdir / os.listdir 的返回顺序。
        items = sorted_paths(
            path for path in source_root.iterdir() if path.is_file()
        )
        if not args.force:
            # TODO: 有断点状态或已完成判定时，在这里过滤掉已完成目标。
            pass

    if target_root is not None:
        target_root.mkdir(parents=True, exist_ok=True)

    if not items:
        log.info("没有需要处理的目标。")
        return EXIT_OK

    log.info("待处理 %d 个目标。", len(items))

    # ------------------------------------------------------------------
    # TODO: 在这里实现实际业务。
    #
    # 1. 确认 I/O-bound 且并发有收益时，用下面的 retry + 线程池组合。
    #    CPU-bound 任务不要走线程池，改用 ProcessPoolExecutor /
    #    multiprocessing，并特别注意 Windows spawn 与 PyInstaller 行为。
    #
    # 2. 调用外部程序（robocopy / ABBYY / ffmpeg 等）时检查
    #    returncode、stdout、stderr、timeout；robocopy 退出码 < 8 视为成功。
    #
    # 3. 生成最终文件时使用「临时文件 → 完整写入 → 验证 → 替换」；
    #    目标在 UNC / 网络路径上时，替换前先备份：
    #
    #        atomic_replace(
    #            target,
    #            temp_path,
    #            verify=verify_output,
    #            source_root=source_root,
    #            allow_direct_overwrite=allow_direct_overwrite,
    #        )
    #
    # 4. 不要根据文件名、修改时间等猜测并删除临时文件，只能清理当前任务
    #    明确创建的临时文件（remove_own_temp_file）。
    # ------------------------------------------------------------------

    @retry(times=retry_count, interval=retry_interval)
    def process_one(item: Path) -> None:
        """占位实现：只记录不修改文件，替换为实际业务逻辑。"""
        log.info("处理：%s", item)

    failures = run_concurrently(
        items,
        process_one,
        thread_count,
        stage="处理",
    )

    # 验证与失败清单。
    if failures:
        report = write_failure_report(failures)
        log.error(
            "有 %d 个任务失败，失败清单：%s；"
            "修复后可运行 --only-failed 只重跑失败项。",
            len(failures),
            report,
        )
        return EXIT_PARTIAL_FAILURE

    log.info("全部任务处理完成。")
    return EXIT_OK


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
    args = build_arg_parser().parse_args()

    try:
        setup_logging()
        exit_code = _run(args)
        log.info("程序执行完成，退出码 %d。", exit_code)
        sys.exit(exit_code)

    except ConfigCreatedError as exc:
        # 配置首次创建不是业务错误，不使用 ERROR 记录。
        log.info("%s", exc)
        print(f"\n{exc}")
        pause_before_exit()
        sys.exit(EXIT_OK)

    except Exception as exc:
        log.exception("[致命错误] 程序无法继续运行：%s", exc)
        print(f"\n[致命错误] 程序无法继续运行：{exc}")
        pause_before_exit()
        sys.exit(EXIT_FATAL)


if __name__ == "__main__":
    # 如果本程序未来改用 multiprocessing / ProcessPoolExecutor，
    # 根据 Windows / PyInstaller 要求在这里加入：
    #
    #     import multiprocessing
    #     multiprocessing.freeze_support()
    #
    # 然后再调用 main()。
    main()
