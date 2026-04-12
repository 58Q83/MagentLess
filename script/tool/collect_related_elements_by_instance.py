#!/usr/bin/env python3
"""Collect selected instance rows from related_elements/loc_outputs.jsonl files.

Given a text file containing one instance_id per line, this script scans project
folders under a results root and extracts matching JSONL rows from:
  <project_dir>/related_elements/loc_outputs.jsonl

Output is a single aggregated JSONL file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


DEFAULT_RESULTS_ROOT = Path("results/gpt5.4mini_with_anonFunc")
DEFAULT_RELATIVE_JSONL = Path("related_elements/loc_outputs.jsonl")
DEFAULT_OUTPUT = Path(
    "results/gpt5.4mini_with_anonFunc/selected_related_elements.jsonl"
)


def load_instance_ids(path: Path) -> List[str]:
    ids: List[str] = []
    seen = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s in seen:
            continue
        seen.add(s)
        ids.append(s)
    if not ids:
        raise ValueError(f"No valid instance_id found in {path}")
    return ids


def discover_project_jsonls(results_root: Path, relative_jsonl: Path) -> List[Path]:
    paths: List[Path] = []
    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        candidate = child / relative_jsonl
        if candidate.exists():
            paths.append(candidate)
    if not paths:
        raise FileNotFoundError(
            f"No project jsonl found under {results_root} with relative path {relative_jsonl}"
        )
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate selected related_elements records by instance_id."
    )
    parser.add_argument(
        "--instance-txt",
        type=Path,
        required=True,
        help="Text file: one instance_id per line.",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=DEFAULT_RESULTS_ROOT,
        help=f"Root containing per-project result folders (default: {DEFAULT_RESULTS_ROOT})",
    )
    parser.add_argument(
        "--relative-jsonl",
        type=Path,
        default=DEFAULT_RELATIVE_JSONL,
        help=f"Relative path from each project folder (default: {DEFAULT_RELATIVE_JSONL})",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output aggregated JSONL (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--strict-missing",
        action="store_true",
        help="Exit non-zero if any instance_id is not found.",
    )
    parser.add_argument(
        "--strict-duplicate",
        action="store_true",
        help="Exit non-zero if one instance_id appears in multiple project JSONLs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.instance_txt.exists():
        raise FileNotFoundError(f"instance txt not found: {args.instance_txt}")
    if not args.results_root.exists():
        raise FileNotFoundError(f"results root not found: {args.results_root}")

    target_ids = load_instance_ids(args.instance_txt)
    target_id_set = set(target_ids)
    project_jsonls = discover_project_jsonls(args.results_root, args.relative_jsonl)

    matched_rows: Dict[str, dict] = {}
    matched_from: Dict[str, str] = {}
    duplicate_hits: Dict[str, List[str]] = {}

    for jsonl_path in project_jsonls:
        source_name = str(jsonl_path.parent.parent.name)
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    raise ValueError(f"Invalid JSONL at {jsonl_path}:{line_no}")

                instance_id = row.get("instance_id")
                if not instance_id or instance_id not in target_id_set:
                    continue

                if instance_id in matched_rows:
                    duplicate_hits.setdefault(instance_id, [matched_from[instance_id]]).append(source_name)
                    continue

                matched_rows[instance_id] = row
                matched_from[instance_id] = source_name

    missing_ids = [iid for iid in target_ids if iid not in matched_rows]

    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.output_jsonl.open("w", encoding="utf-8") as out:
        for iid in target_ids:
            row = matched_rows.get(iid)
            if row is None:
                continue
            out.write(json.dumps(row, ensure_ascii=False))
            out.write("\n")

    print("=== Collect Summary ===")
    print(f"instance_txt:      {args.instance_txt}")
    print(f"results_root:      {args.results_root}")
    print(f"project_jsonl_cnt: {len(project_jsonls)}")
    print(f"target_ids:        {len(target_ids)}")
    print(f"matched:           {len(matched_rows)}")
    print(f"missing:           {len(missing_ids)}")
    print(f"duplicates:        {len(duplicate_hits)}")
    print(f"output_jsonl:      {args.output_jsonl}")

    if missing_ids:
        print("\n[Missing instance_id]")
        for iid in missing_ids[:30]:
            print(f"- {iid}")
        if len(missing_ids) > 30:
            print(f"... and {len(missing_ids) - 30} more")

    if duplicate_hits:
        print("\n[Duplicate instance_id across project folders]")
        for iid, srcs in list(duplicate_hits.items())[:30]:
            print(f"- {iid}: {', '.join(srcs)}")
        if len(duplicate_hits) > 30:
            print(f"... and {len(duplicate_hits) - 30} more")

    if args.strict_missing and missing_ids:
        return 2
    if args.strict_duplicate and duplicate_hits:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
