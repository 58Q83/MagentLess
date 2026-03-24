#!/usr/bin/env python3
"""Print distribution of unique GT function counts per instance."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Set, Tuple


def normalize_path(path: str) -> str:
    return str(path).strip().replace('\\', '/')


def normalize_method_name(name: str) -> str:
    s = re.sub(r"\s+", "", str(name).strip())
    # Keep consistent with evaluate_localization_vs_gt.py
    if "(" in s:
        s = s.split("(", 1)[0]
    if "::" in s:
        s = s.split("::")[-1]
    return s


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print distribution of GT unique function counts per instance."
    )
    parser.add_argument(
        "--gt_path",
        type=str,
        default="data/Def4CAE/Def4CAE_26.2.28.json",
        help="Path to GT JSON file.",
    )
    parser.add_argument(
        "--project_prefix",
        type=str,
        default="",
        help="Optional: only include instance IDs with this prefix, e.g. dealii-",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gt_path = Path(args.gt_path)
    raw = json.loads(gt_path.read_text(encoding="utf-8"))

    dist = Counter()
    included = 0

    for row in raw:
        iid = str(row.get("id", ""))
        if args.project_prefix and not iid.startswith(args.project_prefix):
            continue

        gt_methods = row.get("ground_truth_methods") or []
        funcs: Set[Tuple[str, str]] = set()
        if isinstance(gt_methods, list):
            for m in gt_methods:
                if not isinstance(m, dict):
                    continue
                fpath = normalize_path(m.get("file", ""))
                mname = normalize_method_name(m.get("method_name", ""))
                if fpath and mname:
                    funcs.add((fpath, mname))

        dist[len(funcs)] += 1
        included += 1

    print(f"[INFO] GT path: {gt_path}")
    if args.project_prefix:
        print(f"[INFO] Filter prefix: {args.project_prefix}")
    print(f"[INFO] Included instances: {included}")
    print("[INFO] Distribution of unique GT function count (function_count -> num_instances):")

    for fn_count in sorted(dist):
        print(f"{fn_count}\t{dist[fn_count]}")


if __name__ == "__main__":
    main()
