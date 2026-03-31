#!/usr/bin/env python3
"""Compute file-level Hit Rate for localization results.

Hit definition per instance:
- hit = 1 iff set(pred_files) & set(gold_files) is non-empty
- hit = 0 otherwise

Final metric:
- Hit Rate = total_hits / total_instances
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

DEFAULT_FILE_RES_PATH = Path("file_level/loc_outputs.jsonl")
COMBINED_FILE_RES_PATH = Path("file_level_combined/combined_locs.jsonl")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def normalize_project_name(raw: str) -> str:
    token = raw.strip().lower()
    if token.startswith("openfoam"):
        return "OpenFOAM"
    if token.startswith("occt"):
        return "OCCT"
    if token.startswith("mfem"):
        return "mfem"
    if token.startswith("dealii") or token.startswith("deal.ii"):
        return "dealii"
    return raw.strip()


def infer_project_from_instance_id(instance_id: str) -> str:
    iid = instance_id.lower()
    if iid.startswith("openfoam-dev-"):
        return "OpenFOAM"
    if iid.startswith("occt-"):
        return "OCCT"
    if iid.startswith("mfem-"):
        return "mfem"
    if iid.startswith("dealii-"):
        return "dealii"
    return "UNKNOWN"


def preferred_project_order(name: str) -> Tuple[int, str]:
    preferred = {"OCCT": 0, "OpenFOAM": 1, "mfem": 2, "dealii": 3}
    return (preferred.get(name, 99), name)


def normalize_path(path: str) -> str:
    return str(path).strip().replace("\\", "/")


def dedupe_keep_order(items: Iterable[str]) -> List[str]:
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


def load_gold_file_map(gt_path: Path) -> Dict[str, Set[str]]:
    raw = json.loads(gt_path.read_text(encoding="utf-8"))
    out: Dict[str, Set[str]] = {}

    for row in raw:
        iid = row.get("id")
        if not iid:
            warn("Skipped one GT row because 'id' is missing.")
            continue
        gt_methods = row.get("ground_truth_methods") or []
        if not isinstance(gt_methods, list):
            warn(f"GT instance {iid}: 'ground_truth_methods' is not a list.")
            gt_methods = []

        gold_files: Set[str] = set()
        for m in gt_methods:
            if not isinstance(m, dict):
                continue
            fpath = normalize_path(m.get("file", ""))
            if fpath:
                gold_files.add(fpath)

        out[str(iid)] = gold_files
    return out


def load_pred_file_map(pred_jsonl: Path) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    if not pred_jsonl.exists():
        raise FileNotFoundError(f"Prediction file not found: {pred_jsonl}")

    with pred_jsonl.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                warn(f"JSON decode failed: {pred_jsonl}:{line_no}")
                continue

            iid = row.get("instance_id")
            if not iid:
                warn(f"{pred_jsonl}:{line_no} missing 'instance_id', skipped.")
                continue
            out[str(iid)] = parse_found_files(row.get("found_files"))

    return out


def discover_project_result_dirs(results_root: Path, use_combined_file_res: bool) -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    if not results_root.exists():
        return mapping
    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        project = normalize_project_name(child.name.split("_", 1)[0])
        file_jsonl = child / (COMBINED_FILE_RES_PATH if use_combined_file_res else DEFAULT_FILE_RES_PATH)
        if file_jsonl.exists():
            mapping[project] = child
    return mapping


def print_result_table(rows: List[Tuple[str, int, int]]) -> None:
    print("File-Level Hit Rate")
    print(f"{'Scope':<12} {'Hit':>6} {'Total':>6} {'Hit Rate':>12}")
    print("-" * 40)
    for scope, hit, total in rows:
        rate = (hit / total) if total > 0 else float("nan")
        pct = "NaN" if rate != rate else f"{100.0 * rate:.2f}%"
        print(f"{scope:<12} {hit:>6} {total:>6} {pct:>12}")


def evaluate_by_results_root(gt_map: Dict[str, Set[str]], results_root: Path, use_combined_file_res: bool) -> None:
    gt_ids_by_project: Dict[str, List[str]] = defaultdict(list)
    for iid in gt_map:
        gt_ids_by_project[infer_project_from_instance_id(iid)].append(iid)

    result_dirs = discover_project_result_dirs(results_root, use_combined_file_res)
    if not result_dirs:
        raise RuntimeError(f"No valid result project directory found under: {results_root}")

    all_projects = sorted(set(result_dirs.keys()) & set(gt_ids_by_project.keys()), key=preferred_project_order)
    if not all_projects:
        raise RuntimeError("No overlap between result projects and GT projects.")

    info(f"Projects for evaluation: {', '.join(all_projects)}")
    info(
        "File predictions from: "
        + ("file_level_combined/combined_locs.jsonl" if use_combined_file_res else "file_level/loc_outputs.jsonl")
    )

    hits_by_project: Dict[str, int] = {p: 0 for p in all_projects}
    den_by_project: Dict[str, int] = {p: 0 for p in all_projects}
    unknown_instance_counter = 0

    for project in all_projects:
        pred_path = result_dirs[project] / (COMBINED_FILE_RES_PATH if use_combined_file_res else DEFAULT_FILE_RES_PATH)
        pred_map = load_pred_file_map(pred_path)

        for iid in pred_map:
            if iid not in gt_map:
                unknown_instance_counter += 1

        for iid in gt_ids_by_project[project]:
            gold_files = gt_map.get(iid, set())
            pred_files = set(pred_map.get(iid, []))
            if pred_files & gold_files:
                hits_by_project[project] += 1
            den_by_project[project] += 1

    if unknown_instance_counter > 0:
        warn(f"{unknown_instance_counter} predicted instance_id(s) were not found in GT and skipped.")

    total_hit = sum(hits_by_project.values())
    total_den = sum(den_by_project.values())
    rows: List[Tuple[str, int, int]] = [("overall", total_hit, total_den)]
    rows.extend((p, hits_by_project[p], den_by_project[p]) for p in all_projects)
    print_result_table(rows)


def evaluate_by_single_pred_file(gt_map: Dict[str, Set[str]], pred_jsonl: Path) -> None:
    pred_map = load_pred_file_map(pred_jsonl)
    pred_projects = {infer_project_from_instance_id(iid) for iid in pred_map}
    pred_projects.discard("UNKNOWN")

    # Use GT instances from the same project(s) as predictions to avoid mixing one project with global GT.
    eval_ids: List[str] = []
    for iid in gt_map:
        if infer_project_from_instance_id(iid) in pred_projects:
            eval_ids.append(iid)

    if not eval_ids:
        raise RuntimeError(
            "No GT instances matched predicted instance project prefixes. "
            "Use --results_root mode or check instance_id format."
        )

    hits = 0
    for iid in eval_ids:
        gold_files = gt_map.get(iid, set())
        pred_files = set(pred_map.get(iid, []))
        if pred_files & gold_files:
            hits += 1

    info(f"Single-file mode: {pred_jsonl}")
    info(f"Projects inferred from predictions: {', '.join(sorted(pred_projects, key=preferred_project_order))}")
    print_result_table([("overall", hits, len(eval_ids))])


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute file-level Hit Rate from pred/gold files.")
    parser.add_argument(
        "--gt_path",
        type=str,
        default="data/Def4CAE/filter.json",
        help="Ground-truth JSON path.",
    )
    parser.add_argument(
        "--results_root",
        type=str,
        default="results",
        help="Root folder that contains per-project result directories.",
    )
    parser.add_argument(
        "--pred_jsonl",
        type=str,
        default=None,
        help="Optional single prediction JSONL path. If provided, evaluate in single-file mode.",
    )
    parser.add_argument(
        "--use_combined_file_res",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use file_level_combined/combined_locs.jsonl in --results_root mode (default: true).",
    )
    args = parser.parse_args()

    gt_map = load_gold_file_map(Path(args.gt_path))
    if args.pred_jsonl:
        evaluate_by_single_pred_file(gt_map, Path(args.pred_jsonl))
    else:
        evaluate_by_results_root(gt_map, Path(args.results_root), bool(args.use_combined_file_res))


if __name__ == "__main__":
    main()
