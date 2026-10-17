#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assets/template.py 冒烟自检脚本。

改动模板后运行本脚本，验证关键行为没有退化，而不是只靠阅读确认。

运行：
    python scripts/selfcheck_template.py
    python scripts/selfcheck_template.py --keep      # 保留临时工作目录便于排查

退出码：0 = 全部通过，1 = 存在失败项。

覆盖内容：
01-04  编码回退：UTF-8、UTF-8 BOM、GBK（gb2312 之外的字）、异常类型边界
05-06  带 BOM 的 INI 可正常解析
07-10  重试判定与 retry 装饰器（含 Windows 文件占用 WinError 32）
11-14  日志：INFO 与 WARNING/ERROR 分流、超过上限自动切换新文件
15     无控制台（sys.stdout is None）时不崩溃
16-19  并发：成功、失败即停止提交新任务、失败记录字段
20-24  失败清单：文件名、BOM、表头、读回、取最新
25-28  UNC 判定、备份保留相对路径、替换前备份、明确要求时直接覆盖
29-32  临时文件删除边界
33-35  资源管理器顺序（img2 在 img10 之前）
36-42  端到端：真实运行模板，检查退出码与产物
"""

from __future__ import annotations

import argparse
import codecs
import contextlib
import importlib.util
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = SKILL_ROOT / "assets" / "template.py"

RESULTS: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    flag = "PASS" if ok else "FAIL"
    line = f"[{flag}] {name}"
    if detail and not ok:
        line += f"  -> {detail}"
    print(line)
    sys.stdout.flush()
    return bool(ok)


@contextlib.contextmanager
def patched(module, **values):
    """临时替换模块全局变量。"""
    original = {key: getattr(module, key) for key in values}
    for key, value in values.items():
        setattr(module, key, value)
    try:
        yield
    finally:
        for key, value in original.items():
            setattr(module, key, value)


def load_template():
    spec = importlib.util.spec_from_file_location(
        "template_under_test",
        TEMPLATE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    # 自检阶段先屏蔽模板自身的日志输出，避免与检查结果混在一起；
    # 需要检查日志时由 check_logging 自行挂 handler。
    module.log.propagate = False
    module.log.addHandler(logging.NullHandler())
    return module


def reset_logging(module) -> None:
    for handler in module.log.handlers[:]:
        handler.close()
        module.log.removeHandler(handler)
    # 保留 NullHandler，避免 logging 回退到 lastResort 往 stderr 乱打日志。
    module.log.addHandler(logging.NullHandler())


def set_ini_value(text: str, key: str, value: str) -> str:
    prefix = f"{key} ="
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix) and not stripped.startswith("#"):
            lines.append(f"{key} = {value}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def run_app(app_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(app_dir / "smoke_app.py"), *extra],
        cwd=str(app_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def check_encoding(module, workdir: Path) -> None:
    bom = workdir / "bom.txt"
    bom.write_bytes(codecs.BOM_UTF8 + "中文内容".encode("utf-8"))
    plain = workdir / "plain.txt"
    plain.write_bytes("中文内容".encode("utf-8"))
    gbk = workdir / "gbk.txt"
    gbk.write_bytes("镕测试".encode("gbk"))

    check("01 编码回退：UTF-8 无 BOM", module.read_text_auto(plain) == "中文内容")
    check(
        "02 编码回退：UTF-8 带 BOM 不残留 \\ufeff",
        module.read_text_auto(bom) == "中文内容",
        f"实际={module.read_text_auto(bom)!r}",
    )
    check(
        "03 编码回退：GBK（含 gb2312 之外的字）",
        module.read_text_auto(gbk) == "镕测试",
    )

    # 证明旧实现（只用 utf-8 / gb2312）确实会失败
    legacy_ok = False
    try:
        gbk.read_text(encoding="gb2312")
        legacy_ok = True
    except UnicodeDecodeError:
        legacy_ok = False
    check(
        "04 旧实现（gb2312）会失败（证明修复必要）",
        legacy_ok is False,
        "gb2312 竟然可解码，说明测试数据不合适",
    )

    missing_raised = False
    try:
        module.read_text_auto(workdir / "not_exist.txt")
    except FileNotFoundError:
        missing_raised = True
    check("05 文件不存在时不被编码回退掩盖", missing_raised)

    ini_dir = workdir / "ini"
    ini_dir.mkdir()
    ini_path = ini_dir / "smoke_app.ini"
    ini_path.write_bytes(
        codecs.BOM_UTF8 + "[CONFIG]\nTHREAD_COUNT = 2\n".encode("utf-8")
    )
    with patched(module, CONFIG_PATH=ini_path):
        config = module.load_config()
    check(
        "06 带 BOM 的 INI 可正常解析",
        config.getint("CONFIG", "THREAD_COUNT") == 2,
    )


def check_retry(module) -> None:
    def winerror(exc_type, code, message="被其他进程占用"):
        exc = exc_type(message)
        exc.winerror = code
        return exc

    check(
        "07 WinError 32/33 判定为可重试",
        module.is_transient(winerror(PermissionError, 32))
        and module.is_transient(winerror(OSError, 33)),
    )
    check(
        "08 权限/参数/路径错误判定为不可重试",
        not module.is_transient(winerror(PermissionError, 5))
        and not module.is_transient(ValueError("bad"))
        and not module.is_transient(FileNotFoundError("missing")),
    )
    check(
        "09 TimeoutError / ConnectionError 判定为可重试",
        module.is_transient(TimeoutError("timeout"))
        and module.is_transient(ConnectionError("reset")),
    )

    state = {"calls": 0}

    @module.retry(times=3, interval=0)
    def flaky():
        state["calls"] += 1
        if state["calls"] < 3:
            raise TimeoutError("临时超时")
        return "ok"

    check(
        "10 可重试异常恢复成功且重试 3 次",
        flaky() == "ok" and state["calls"] == 3,
        f"calls={state['calls']}",
    )

    permanent = {"calls": 0}

    @module.retry(times=3, interval=0)
    def permanent_fail():
        permanent["calls"] += 1
        raise ValueError("参数错误不可重试")

    raised = False
    try:
        permanent_fail()
    except ValueError:
        raised = True
    check(
        "11 不可重试异常立即失败（只调用 1 次）",
        raised and permanent["calls"] == 1,
        f"calls={permanent['calls']}",
    )

    exhausted = {"calls": 0}

    @module.retry(times=2, interval=0)
    def always_timeout():
        exhausted["calls"] += 1
        raise TimeoutError("一直超时")

    raised = False
    try:
        always_timeout()
    except TimeoutError:
        raised = True
    check(
        "12 次数用尽后抛出原始异常",
        raised and exhausted["calls"] == 2,
        f"calls={exhausted['calls']}",
    )

    locked = {"calls": 0}

    @module.retry(times=2, interval=0)
    def locked_file():
        locked["calls"] += 1
        if locked["calls"] == 1:
            raise winerror(PermissionError, 32)
        return "ok"

    check(
        "13 文件被占用（WinError 32）默认参与重试",
        locked_file() == "ok" and locked["calls"] == 2,
        f"calls={locked['calls']}",
    )

    invalid_raised = False
    try:
        module.retry(times=0)
    except ValueError:
        invalid_raised = True
    check("14 times < 1 时立即报错", invalid_raised)


def check_logging(module, workdir: Path) -> None:
    log_dir = workdir / "logs"
    log_dir.mkdir()

    with patched(module, APP_DIR=log_dir, APP_NAME="smoke"):
        info_path, error_path = module.setup_logging()
        module.log.info("信息行")
        module.log.warning("警告行")
        module.log.error("错误行")
        reset_logging(module)

    info_text = info_path.read_text(encoding="utf-8")
    error_text = error_path.read_text(encoding="utf-8")

    check(
        "15 信息日志只含 INFO",
        "信息行" in info_text
        and "警告行" not in info_text
        and "错误行" not in info_text,
        info_text.strip(),
    )
    check(
        "16 错误日志含 WARNING 与 ERROR",
        "警告行" in error_text and "错误行" in error_text,
        error_text.strip(),
    )
    check(
        "17 日志文件名带时间戳",
        re.match(r"^smoke_info_\d{8}_\d{6}\.log$", info_path.name) is not None
        and re.match(r"^smoke_error_\d{8}_\d{6}\.log$", error_path.name) is not None,
        f"{info_path.name} / {error_path.name}",
    )

    # 单文件超过上限时切换新文件
    roll_dir = workdir / "roll"
    roll_dir.mkdir()
    handler = module.SizeRollingTimestampHandler(
        roll_dir, "roll", "info", max_bytes=400
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    roll_log = logging.getLogger("selfcheck_roll")
    roll_log.setLevel(logging.INFO)
    roll_log.propagate = False
    roll_log.addHandler(handler)
    for index in range(60):
        roll_log.info("第 %d 行 %s", index, "x" * 20)
    handler.close()
    roll_log.removeHandler(handler)

    paths = handler.all_paths()
    total_lines = sum(
        len(path.read_text(encoding="utf-8").splitlines()) for path in paths
    )
    check(
        "18 超过 10 MB 上限时切换新时间戳文件（此处用小上限验证）",
        len(paths) >= 2
        and all(
            re.match(r"^roll_info_\d{8}_\d{6}(_\d+)?\.log$", path.name)
            for path in paths
        ),
        f"文件数={len(paths)}",
    )
    check(
        "19 切换过程中不丢日志",
        total_lines == 60,
        f"行数={total_lines}",
    )

    # 无控制台环境：pythonw / --noconsole 下 sys.stdout 为 None
    with patched(module, APP_DIR=log_dir, APP_NAME="nocons"):
        saved_stdout = sys.stdout
        sys.stdout = None
        try:
            module.setup_logging()
            module.log.info("无控制台环境下的日志")
            handlers = list(module.log.handlers)
        finally:
            sys.stdout = saved_stdout
            reset_logging(module)

    check(
        "20 sys.stdout 为 None 时不崩溃且不挂控制台 handler",
        not any(type(handler) is logging.StreamHandler for handler in handlers),
        f"handlers={[type(h).__name__ for h in handlers]}",
    )


def check_concurrency(module) -> None:
    processed = []
    failures = module.run_concurrently(
        list(range(5)), lambda item: processed.append(item), 2
    )
    check(
        "21 全部成功时返回空失败清单",
        failures == [] and sorted(processed) == [0, 1, 2, 3, 4],
        f"failures={failures}, processed={processed}",
    )

    seen = []

    def failing_worker(item):
        seen.append(item)
        if item == 3:
            raise ValueError("boom")

    failures = module.run_concurrently(
        list(range(10)), failing_worker, 1, stage="处理"
    )
    check(
        "22 失败即停止提交新任务（后续任务不再执行）",
        seen == [0, 1, 2, 3],
        f"processed={seen}",
    )
    check(
        "23 失败记录包含原路径/阶段/摘要/可重试",
        len(failures) == 1
        and failures[0].source == "3"
        and failures[0].stage == "处理"
        and failures[0].error == "ValueError: boom"
        and failures[0].retryable is False,
        f"{failures}",
    )

    seen_all = []

    def failing_worker_all(item):
        seen_all.append(item)
        if item == 3:
            raise ValueError("boom")

    failures_all = module.run_concurrently(
        list(range(10)), failing_worker_all, 1, stop_on_error=False
    )
    check(
        "24 stop_on_error=False 时跑完全部并汇总失败",
        len(seen_all) == 10 and len(failures_all) == 1,
        f"processed={len(seen_all)}, failures={len(failures_all)}",
    )


def check_failure_report(module, workdir: Path) -> None:
    report_dir = workdir / "reports"
    report_dir.mkdir()
    report_dir_check = module.write_failure_report(
        [],
        directory=report_dir,
    )
    check("25 没有失败时不产生清单文件", report_dir_check is None)

    records = [
        module.FailureRecord(
            source=r"C:\数据\扫描件 01.pdf",
            stage="OCR",
            error="RuntimeError: 无法写入",
            retryable=True,
        )
    ]
    report = module.write_failure_report(records, directory=report_dir)
    check(
        "26 失败清单文件名 failed_YYYYmmdd_HHMMSS.csv",
        report is not None
        and re.match(r"^failed_\d{8}_\d{6}\.csv$", report.name) is not None,
        f"{report.name if report else None}",
    )
    check(
        "27 失败清单使用 utf-8-sig（Excel 双击不乱码）",
        report.read_bytes().startswith(codecs.BOM_UTF8),
    )
    header = report.read_text(encoding="utf-8-sig").splitlines()[0]
    check(
        "28 失败清单表头为约定四列",
        header == "原路径,失败阶段,异常摘要,是否可重试",
        header,
    )
    check(
        "29 失败清单可被 --only-failed 读回",
        module.load_failure_report(report) == [r"C:\数据\扫描件 01.pdf"],
    )
    check(
        "30 自动选择最新失败清单",
        module.latest_failure_report(report_dir) == report,
    )


def check_network_and_backup(module, workdir: Path) -> None:
    check(
        "31 UNC / 网络路径判定正确",
        module.is_network_path(r"\\server\share\a.txt")
        and module.is_network_path("//server/share/a.txt")
        and not module.is_network_path(r"C:\a.txt")
        and not module.is_network_path(r"\\?\C:\a.txt")
        and not module.is_network_path(r"\\.\C:"),
    )

    source_root = workdir / "src"
    (source_root / "sub").mkdir(parents=True)
    victim = source_root / "sub" / "a.txt"
    victim.write_text("原始内容", encoding="utf-8")

    backup_root = workdir / "bk"
    backup_path = module.backup_file(victim, source_root, backup_root)
    check(
        "32 备份保留相对路径且内容一致",
        backup_path == backup_root / "sub" / "a.txt"
        and backup_path.read_text(encoding="utf-8") == "原始内容",
        f"{backup_path}",
    )

    temp_path = workdir / "new.txt"
    temp_path.write_text("新内容", encoding="utf-8")
    with patched(module, is_network_path=lambda _path: True):
        replaced_backup = module.atomic_replace(
            victim,
            temp_path,
            source_root=source_root,
            backup_root=backup_root,
        )
    check(
        "33 网络路径替换前先备份，目标被替换",
        replaced_backup is not None
        and replaced_backup.read_text(encoding="utf-8") == "原始内容"
        and victim.read_text(encoding="utf-8") == "新内容",
    )

    temp_path_2 = workdir / "new2.txt"
    temp_path_2.write_text("第二版", encoding="utf-8")
    victim.write_text("第二版原文件", encoding="utf-8")
    with patched(module, is_network_path=lambda _path: True):
        no_backup = module.atomic_replace(
            victim,
            temp_path_2,
            source_root=source_root,
            backup_root=backup_root,
            allow_direct_overwrite=True,
        )
    check(
        "34 用户明确要求时可直接覆盖（不备份）",
        no_backup is None and victim.read_text(encoding="utf-8") == "第二版",
    )

    def bad_verify(_path):
        raise ValueError("校验失败")

    temp_path_3 = workdir / "new3.txt"
    temp_path_3.write_text("坏数据", encoding="utf-8")
    raised = False
    try:
        module.atomic_replace(victim, temp_path_3, verify=bad_verify)
    except ValueError:
        raised = True
    check(
        "35 校验失败时目标文件保持原样",
        raised and victim.read_text(encoding="utf-8") == "第二版",
    )


def check_temp_deletion(module, workdir: Path) -> None:
    temp_root = workdir / "tmpwork"
    temp_root.mkdir()
    inside = temp_root / "x.tmp"
    inside.write_text("t", encoding="utf-8")
    outside = workdir / "keep.txt"
    outside.write_text("k", encoding="utf-8")

    check(
        "36 可删除当前任务临时目录内的文件",
        module.remove_own_temp_file(inside, temp_root) and not inside.exists(),
    )
    check(
        "37 不删除临时目录之外的文件",
        not module.remove_own_temp_file(outside, temp_root) and outside.exists(),
    )
    check(
        "38 不删除临时目录自身",
        not module.remove_own_temp_file(temp_root, temp_root),
    )

    network_file = temp_root / "y.tmp"
    network_file.write_text("t", encoding="utf-8")
    with patched(module, is_network_path=lambda _path: True):
        removed = module.remove_own_temp_file(network_file, temp_root)
    check(
        "39 网络路径下不执行删除",
        removed is False and network_file.exists(),
    )


def check_sorting(module) -> None:
    names = [
        "img10.jpg",
        "img2.jpg",
        "img1.jpg",
        "IMG3.jpg",
        "a1b10.txt",
        "a1b2.txt",
    ]
    expected = [
        "a1b2.txt",
        "a1b10.txt",
        "img1.jpg",
        "img2.jpg",
        "IMG3.jpg",
        "img10.jpg",
    ]
    got = [path.name for path in module.sorted_paths(names)]
    check(
        "40 排序与资源管理器一致（img2 在 img10 之前）",
        got == expected,
        f"实际={got}",
    )
    check(
        "41 字典序确实会排错（证明规范必要）",
        sorted(names)[-1] == "img2.jpg",
        f"字典序={sorted(names)}",
    )

    fallback_names = sorted(names, key=module._NaturalKey)
    probe = ["p1.txt", "p01.txt", "p001.txt"]
    tie_ok = sorted(probe, key=module._NaturalKey) == sorted(
        probe, key=module.explorer_sort_key
    )
    check(
        "42 纯 Python 回退与资源管理器同序（含前导零平局）",
        fallback_names == expected and tie_ok,
        f"回退={fallback_names}, 平局一致={tie_ok}",
    )


def check_end_to_end(module, workdir: Path) -> None:
    app_dir = workdir / "app"
    app_dir.mkdir()
    shutil.copy2(TEMPLATE_PATH, app_dir / "smoke_app.py")

    first = run_app(app_dir)
    ini_path = app_dir / "smoke_app.ini"
    check(
        "43 首次运行创建 INI 并正常退出",
        first.returncode == 0 and ini_path.exists(),
        f"rc={first.returncode}, stderr={first.stderr[-300:]}",
    )

    data_dir = app_dir / "data"
    data_dir.mkdir()
    (data_dir / "img10.txt").write_text("", encoding="utf-8")
    (data_dir / "img2.txt").write_text("", encoding="utf-8")
    (data_dir / "img1.txt").write_text("", encoding="utf-8")

    ini_text = set_ini_value(
        ini_path.read_text(encoding="utf-8"), "SOURCE_ROOT", str(data_dir)
    )
    ini_path.write_text(ini_text, encoding="utf-8")

    second = run_app(app_dir)
    info_logs = sorted(app_dir.glob("smoke_app_info_*.log"))
    error_logs = sorted(app_dir.glob("smoke_app_error_*.log"))
    check(
        "44 正常运行退出码 0 且生成双日志",
        second.returncode == 0
        and len(info_logs) >= 1
        and len(info_logs) == len(error_logs),
        f"rc={second.returncode}, info={len(info_logs)}, error={len(error_logs)}, "
        f"stderr={second.stderr[-300:]}",
    )

    # 时间戳文件名可直接按名称排序，取最后一个即本次运行写入的日志。
    info_text = info_logs[-1].read_text(encoding="utf-8") if info_logs else ""
    order_ok = (
        info_text.find("img2.txt") != -1
        and info_text.find("img10.txt") != -1
        and info_text.find("img2.txt") < info_text.find("img10.txt")
        and info_text.find("img1.txt") < info_text.find("img2.txt")
    )
    check(
        "45 端到端枚举顺序与资源管理器一致",
        order_ok,
        info_text.strip()[-300:],
    )

    ini_path.write_text(
        set_ini_value(ini_text, "SOURCE_ROOT", str(app_dir / "not_exist")),
        encoding="utf-8",
    )
    third = run_app(app_dir)
    check(
        "46 输入不存在时以退出码 1 结束（致命错误）",
        third.returncode == 1,
        f"rc={third.returncode}",
    )

    ini_path.write_text(ini_text, encoding="utf-8")
    fourth = run_app(app_dir, "--only-failed")
    check(
        "47 --only-failed 且无失败清单时以退出码 1 结束",
        fourth.returncode == 1,
        f"rc={fourth.returncode}",
    )

    parser = module.build_arg_parser()
    args = parser.parse_args(["--yes", "--force"])
    check(
        "48 命令行参数可解析",
        args.assume_yes is True and args.force is True,
        f"{args}",
    )

    # 部分失败路径：替换 run_concurrently，验证退出码 2 与失败清单产出
    fake_dir = workdir / "app_fake"
    fake_dir.mkdir()
    fake_ini = fake_dir / "smoke_app.ini"
    fake_ini.write_text(
        "[CONFIG]\nSOURCE_ROOT = " + str(data_dir) + "\n",
        encoding="utf-8",
    )
    fake_records = [
        module.FailureRecord(
            source=str(data_dir / "img1.txt"),
            stage="处理",
            error="ValueError: boom",
            retryable=False,
        )
    ]
    with patched(
        module,
        APP_DIR=fake_dir,
        CONFIG_PATH=fake_ini,
        APP_NAME="smoke_app",
        run_concurrently=lambda *a, **k: list(fake_records),
    ):
        exit_code = module._run(parser.parse_args(["--yes"]))
    reports = sorted(fake_dir.glob("failed_*.csv"))
    check(
        "49 部分失败返回退出码 2 并写出失败清单",
        exit_code == module.EXIT_PARTIAL_FAILURE and len(reports) == 1,
        f"code={exit_code}, reports={[p.name for p in reports]}",
    )
    check(
        "50 失败清单可回灌到下一次运行",
        reports
        and module.load_failure_report(reports[0]) == [str(data_dir / "img1.txt")],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="template.py 冒烟自检")
    parser.add_argument("--keep", action="store_true", help="保留临时工作目录")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="指定工作目录；默认使用系统临时目录下的新建目录",
    )
    options = parser.parse_args()

    if not TEMPLATE_PATH.exists():
        print(f"找不到模板：{TEMPLATE_PATH}")
        return 1

    # 控制台输出统一 UTF-8，避免中文在 GBK 控制台下乱码。
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    # 每次运行使用独立子目录，避免上一次运行的残留文件互相干扰。
    # --workdir 只作为父目录，脚本始终只清理自己创建的那一层。
    if options.workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="skill_selfcheck_"))
    else:
        parent_dir = Path(options.workdir)
        parent_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workdir = parent_dir / f"run_{stamp}"
        workdir.mkdir(parents=True, exist_ok=True)
    print(f"临时工作目录：{workdir}")
    print(f"模板：{TEMPLATE_PATH}")

    module = None
    try:
        module = load_template()
        check_encoding(module, workdir)
        check_retry(module)
        check_logging(module, workdir)
        check_concurrency(module)
        check_failure_report(module, workdir)
        check_network_and_backup(module, workdir)
        check_temp_deletion(module, workdir)
        check_sorting(module)
        check_end_to_end(module, workdir)
    finally:
        if module is not None:
            reset_logging(module)
        if options.keep:
            print(f"已保留临时工作目录：{workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    failed = [item for item in RESULTS if not item[1]]
    print("-" * 60)
    print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
    for name, _, detail in failed:
        print(f"  FAIL {name}  {detail}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
