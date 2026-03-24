#!/usr/bin/env python3
"""Export file-level prediction vs GT details by instance_id for manual inspection.

Default inputs are configured for dealii, but project prefix is configurable.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Set


DEFAULT_TOPK = (1, 3, 5)


def normalize_path(path: str) -> str:
    return str(path).strip().replace("\\", "/")


def dedupe_keep_order(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def parse_found_files(value) -> List[str]:
    files: List[str] = []
    if value is None:
        return files
    candidates = value if isinstance(value, list) else str(value).splitlines()
    for item in candidates:
        s = str(item).strip()
        if not s or s == "```":
            continue
        files.append(normalize_path(s))
    return dedupe_keep_order(files)


def load_gt_file_sets(gt_path: Path, project_prefix: str) -> Dict[str, Set[str]]:
    raw = json.loads(gt_path.read_text(encoding="utf-8"))
    out: Dict[str, Set[str]] = {}
    for row in raw:
        iid = str(row.get("id", ""))
        if not iid.startswith(project_prefix):
            continue
        gt_methods = row.get("ground_truth_methods") or []
        files: Set[str] = set()
        if isinstance(gt_methods, list):
            for m in gt_methods:
                if not isinstance(m, dict):
                    continue
                fpath = normalize_path(m.get("file", ""))
                if fpath:
                    files.add(fpath)
        out[iid] = files
    return out


def load_pred_file_lists(pred_jsonl: Path, project_prefix: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    if not pred_jsonl.exists():
        return out
    with pred_jsonl.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON decode failed at {pred_jsonl}:{line_no}: {exc}") from exc
            iid = str(row.get("instance_id", ""))
            if not iid.startswith(project_prefix):
                continue
            out[iid] = parse_found_files(row.get("found_files"))
    return out


def compute_first_hit_rank(pred_files: List[str], gt_files: Set[str]) -> int | None:
    for idx, path in enumerate(pred_files, start=1):
        if path in gt_files:
            return idx
    return None


def build_instance_row(instance_id: str, gt_files: Set[str], pred_files: List[str], topk: List[int]) -> dict:
    gt_sorted = sorted(gt_files)
    pred = list(pred_files)
    gt_set = set(gt_sorted)
    pred_set = set(pred)

    intersection_in_pred_order = [p for p in pred if p in gt_set]
    missing_gt = [g for g in gt_sorted if g not in pred_set]
    extra_pred = [p for p in pred if p not in gt_set]

    hit_at = {}
    recall_at = {}
    for k in topk:
        cut = pred[:k]
        hit = any(p in gt_set for p in cut)
        covered = len({p for p in cut if p in gt_set})
        hit_at[f"hit@{k}"] = int(hit)
        recall_at[f"recall@{k}"] = (covered / len(gt_set)) if gt_set else None

    return {
        "instance_id": instance_id,
        "gt_file_count": len(gt_sorted),
        "pred_file_count": len(pred),
        "gt_files": gt_sorted,
        "pred_files": pred,
        "intersection_in_pred_order": intersection_in_pred_order,
        "intersection_count": len(intersection_in_pred_order),
        "missing_gt_files": missing_gt,
        "extra_pred_files": extra_pred,
        "first_hit_rank": compute_first_hit_rank(pred, gt_set),
        "metrics": {
            **hit_at,
            **recall_at,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export file-level prediction vs GT by instance_id.")
    parser.add_argument(
        "--gt_path",
        type=str,
        default="data/Def4CAE/Def4CAE_26.2.28.json",
        help="Path to GT JSON.",
    )
    parser.add_argument(
        "--pred_jsonl",
        type=str,
        default="results/dealii.gpt5.4mini/file_level/loc_outputs.jsonl",
        help="Path to file-level prediction JSONL.",
    )
    parser.add_argument(
        "--project_prefix",
        type=str,
        default="dealii-",
        help="Only include instance IDs that start with this prefix.",
    )
    parser.add_argument(
        "--topk",
        nargs="+",
        type=int,
        default=list(DEFAULT_TOPK),
        help="Top-k values for quick metrics in output.",
    )
    parser.add_argument(
        "--output_json",
        type=str,
        default="results/analysis/dealii_file_level_vs_gt.json",
        help="Output JSON path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    topk = sorted(set(k for k in args.topk if k > 0))
    if not topk:
        raise ValueError("--topk must contain at least one positive integer")

    gt_path = Path(args.gt_path)
    pred_jsonl = Path(args.pred_jsonl)
    output_json = Path(args.output_json)

    gt_map = load_gt_file_sets(gt_path, args.project_prefix)
    pred_map = load_pred_file_lists(pred_jsonl, args.project_prefix)

    all_ids = sorted(set(gt_map.keys()) | set(pred_map.keys()))

    rows = []
    for iid in all_ids:
        gt_files = gt_map.get(iid, set())
        pred_files = pred_map.get(iid, [])
        row = build_instance_row(iid, gt_files, pred_files, topk)
        row["has_gt"] = iid in gt_map
        row["has_prediction"] = iid in pred_map
        rows.append(row)

    output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "project_prefix": args.project_prefix,
        "gt_path": str(gt_path),
        "pred_jsonl": str(pred_jsonl),
        "topk": topk,
        "num_instances": len(rows),
        "instances": rows,
    }
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] wrote {len(rows)} instance rows -> {output_json}")


if __name__ == "__main__":
    main()
