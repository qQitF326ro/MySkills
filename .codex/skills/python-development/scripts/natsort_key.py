#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资源管理器顺序排序工具（可导入，也可直接当命令行工具用）。

规范说明见 references/windows-packaging.md「文件枚举与排序」：
批量处理的枚举顺序必须与 Windows 资源管理器一致，即数字段按数值比较，
img2 排在 img10 之前；默认的 sorted() 字典序做不到这一点。

实现方式：
- Windows 上直接调用 shlwapi.StrCmpLogicalW，与资源管理器完全一致；
- 其他平台或 API 不可用时，退回到等价的纯 Python 自然排序 key。

用法：
    from natsort_key import sorted_paths

    for path in sorted_paths(source_dir.iterdir()):
        ...

命令行：
    python scripts/natsort_key.py 目录或文件...            # 排序后逐行输出路径
    python scripts/natsort_key.py --names img2 img10 img1   # 只排序给定名称
    python scripts/natsort_key.py --self-test               # 自检

注意：assets/template.py 里内置了一份等价实现（explorer_sort_key /
sorted_paths），因为单文件 EXE 必须自包含。两者行为必须保持一致，
修改其中一处时同步另一处，并运行 scripts/selfcheck_template.py 与
本脚本的 --self-test 验证。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable


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


STRCMP_LOGICAL = _load_strcmp_logical()


class NaturalKey:
    """纯 Python 自然排序 key。

    数字段按数值比较，其余部分不区分大小写；数值相同（p1 与 p01）时用
    原字符串做字典序 tiebreak，与 ExplorerKey 的规则保持一致，
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

    def __lt__(self, other: "NaturalKey") -> bool:
        if not isinstance(other, NaturalKey):
            return NotImplemented
        if self.parts != other.parts:
            return self.parts < other.parts
        return self.name < other.name

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, NaturalKey):
            return NotImplemented
        return self.parts == other.parts and self.name == other.name


class ExplorerKey:
    """按资源管理器规则比较名称的排序 key。"""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __lt__(self, other: "ExplorerKey") -> bool:
        if not isinstance(other, ExplorerKey):
            return NotImplemented
        result = STRCMP_LOGICAL(self.name, other.name)
        if result == 0:
            return self.name < other.name
        return result < 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExplorerKey):
            return NotImplemented
        return STRCMP_LOGICAL(self.name, other.name) == 0


def explorer_key(name: str):
    """返回与资源管理器一致的排序 key。"""
    if STRCMP_LOGICAL is not None:
        return ExplorerKey(name)
    return NaturalKey(name)


def sorted_names(names: Iterable[str]) -> list[str]:
    """按资源管理器顺序排序名称。"""
    return sorted(names, key=explorer_key)


def sorted_paths(
    paths: Iterable[Path | str],
    *,
    by_name: bool = True,
) -> list[Path]:
    """按资源管理器顺序排序路径；by_name 为假时按完整路径比较。"""
    return sorted(
        (Path(path) for path in paths),
        key=lambda item: explorer_key(item.name if by_name else str(item)),
    )


def _self_test() -> int:
    failures = 0
    expected_names = [
        "a1b2.txt",
        "a1b10.txt",
        "img1.jpg",
        "img2.jpg",
        "IMG3.jpg",
        "img10.jpg",
    ]
    actual = sorted_names(
        ["img10.jpg", "img2.jpg", "img1.jpg", "IMG3.jpg", "a1b10.txt", "a1b2.txt"]
    )
    if actual != expected_names:
        print(f"FAIL 资源管理器顺序：期望 {expected_names}，实际 {actual}")
        failures += 1
    else:
        print("PASS 资源管理器顺序（img2 在 img10 之前）")

    if sorted(["img10.jpg", "img2.jpg"])[-1] != "img2.jpg":
        print("FAIL 字典序对照组应排在 img2.jpg 结尾")
        failures += 1
    else:
        print("PASS 字典序对照（证明默认 sorted() 不可用）")

    fallback = sorted(
        ["img10.jpg", "img2.jpg", "img1.jpg", "IMG3.jpg", "a1b10.txt", "a1b2.txt"],
        key=NaturalKey,
    )
    if fallback != expected_names:
        print(f"FAIL 纯 Python 回退：实际 {fallback}")
        failures += 1
    else:
        print("PASS 纯 Python 回退与 StrCmpLogicalW 同序")

    probe = ["p1.txt", "p01.txt", "p001.txt"]
    explorer_order = sorted_names(probe)
    fallback_order = sorted(probe, key=NaturalKey)
    if explorer_order != fallback_order or len(set(explorer_order)) != 3:
        print(
            f"FAIL 前导零平局规则不一致："
            f"资源管理器={explorer_order}，回退={fallback_order}"
        )
        failures += 1
    else:
        print(f"PASS 前导零平局规则一致且稳定（{explorer_order}）")

    print(f"\n共 4 项，失败 {failures} 项（可用 API：{STRCMP_LOGICAL is not None}）")
    return 1 if failures else 0


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(
        description="按 Windows 资源管理器顺序排序文件或名称",
    )
    parser.add_argument("paths", nargs="*", help="待排序的目录或文件")
    parser.add_argument(
        "--names",
        action="store_true",
        help="把参数当作纯名称排序，不访问文件系统",
    )
    parser.add_argument("--self-test", action="store_true", help="运行自检")
    options = parser.parse_args()

    if options.self_test:
        return _self_test()

    if not options.paths:
        parser.print_help()
        return 1

    if options.names:
        for name in sorted_names(options.paths):
            print(name)
        return 0

    entries: list[Path] = []
    for raw in options.paths:
        path = Path(raw)
        if path.is_dir():
            entries.extend(path.iterdir())
        else:
            entries.append(path)

    for path in sorted_paths(entries):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
