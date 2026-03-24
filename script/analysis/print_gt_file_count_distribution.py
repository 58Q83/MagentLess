#!/usr/bin/env python3
"""Print distribution of unique GT file counts per instance."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def normalize_path(path: str) -> str:
    return str(path).strip().replace('\\', '/')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print distribution of GT unique file counts."
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
        files = set()
        if isinstance(gt_methods, list):
            for m in gt_methods:
                if not isinstance(m, dict):
                    continue
                fpath = normalize_path(m.get("file", ""))
                if fpath:
                    files.add(fpath)
        if len(files) >= 5:
            print(f"id:{iid} count:{len(files)}")
        dist[len(files)] += 1
        included += 1

    print(f"[INFO] GT path: {gt_path}")
    if args.project_prefix:
        print(f"[INFO] Filter prefix: {args.project_prefix}")
    print(f"[INFO] Included instances: {included}")
    print("[INFO] Distribution of unique GT file count (file_count -> num_instances):")

    for file_count in sorted(dist):
        print(f"{file_count}\t{dist[file_count]}")


if __name__ == "__main__":
    main()
