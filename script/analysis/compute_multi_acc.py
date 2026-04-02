#!/usr/bin/env python3
"""计算多目标 Acc@k 指标并打印到终端。

指标：
文件多目标实例(|GT_files| >= 2):Acc@5
函数多目标实例(|GT_functions| >= 2):Acc@10, Acc@15
单个实例的 Acc@k 定义：
如果真值集合(GT_set)被前 k 个预测结果完全覆盖，则得分为 1。
否则，得分为 0。
最终指标：
Acc@k = 总得分 / 总实例数
缺失的预测将被视为预测为空。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

DEFAULT_FILE_RES_PATH = Path("file_level/loc_outputs.jsonl")
COMBINED_FILE_RES_PATH = Path("file_level_combined/combined_locs.jsonl")
RELATED_RES_PATH = Path("related_elements/loc_outputs.jsonl")


@dataclass
class GTInstance:
    instance_id: str
    project: str
    gt_files: Set[str]
    gt_functions: Set[Tuple[str, str]]


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
    return path.strip().replace("\\", "/")


def normalize_method_name(name: str) -> str:
    s = re.sub(r"\s+", "", name.strip())
    if "(" in s:
        s = s.split("(", 1)[0]
    if "::" in s:
        s = s.split("::")[-1]
    return s


def dedupe_keep_order(items: Iterable) -> List:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def load_jsonl(path: Path) -> List[dict]:
    rows: List[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                warn(f"JSON decode failed: {path}:{i}")
    return rows


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


def parse_function_entries_from_chunk(chunk: str, file_path: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    fpath = normalize_path(file_path)
    for raw_line in str(chunk).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("function:"):
            fn = line.split(":", 1)[1].strip()
            fn = normalize_method_name(fn)
            if fn:
                out.append((fpath, fn))
    return out


def parse_found_related_locs(value) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    if not isinstance(value, dict):
        return pairs
    for file_path, entries in value.items():
        if entries is None:
            continue
        chunks = entries if isinstance(entries, list) else [entries]
        for chunk in chunks:
            pairs.extend(parse_function_entries_from_chunk(str(chunk), str(file_path)))
    return dedupe_keep_order(pairs)


def load_gt(gt_path: Path) -> Dict[str, GTInstance]:
    with gt_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    gt_map: Dict[str, GTInstance] = {}
    for row in raw:
        instance_id = row.get("id")
        if not instance_id:
            warn("Skipped one GT row because 'id' is missing.")
            continue

        project = infer_project_from_instance_id(instance_id)
        gt_methods = row.get("ground_truth_methods") or []
        if not isinstance(gt_methods, list):
            warn(f"GT instance {instance_id}: 'ground_truth_methods' is not a list.")
            gt_methods = []

        gt_files: Set[str] = set()
        gt_funcs: Set[Tuple[str, str]] = set()
        for m in gt_methods:
            if not isinstance(m, dict):
                continue
            fpath = normalize_path(str(m.get("file", "")).strip())
            mname = normalize_method_name(str(m.get("method_name", "")).strip())
            if fpath:
                gt_files.add(fpath)
            if fpath and mname:
                gt_funcs.add((fpath, mname))

        gt_map[instance_id] = GTInstance(
            instance_id=instance_id,
            project=project,
            gt_files=gt_files,
            gt_functions=gt_funcs,
        )
    return gt_map


def discover_project_result_dirs(results_root: Path, use_combined_file_res: bool) -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    if not results_root.exists():
        return mapping

    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        project = normalize_project_name(child.name.split("_", 1)[0])
        file_jsonl = child / (COMBINED_FILE_RES_PATH if use_combined_file_res else DEFAULT_FILE_RES_PATH)
        related_jsonl = child / RELATED_RES_PATH
        if file_jsonl.exists() or related_jsonl.exists():
            mapping[project] = child
    return mapping


def load_file_predictions(path: Path) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for row in load_jsonl(path):
        iid = row.get("instance_id")
        if not iid:
            warn(f"{path}: one row missing 'instance_id', skipped.")
            continue
        out[iid] = parse_found_files(row.get("found_files"))
    return out


def load_function_predictions(path: Path) -> Dict[str, List[Tuple[str, str]]]:
    out: Dict[str, List[Tuple[str, str]]] = {}
    for row in load_jsonl(path):
        iid = row.get("instance_id")
        if not iid:
            warn(f"{path}: one row missing 'instance_id', skipped.")
            continue
        out[iid] = parse_found_related_locs(row.get("found_related_locs"))
    return out


def compute_acc_at_k(pred_list: Sequence, gt_set: Set, k: int) -> int:
    return int(gt_set.issubset(set(pred_list[:k])))


def safe_rate(num: int, den: int) -> float:
    return (num / den) if den > 0 else float("nan")


def fmt_pct(rate: float) -> str:
    if rate != rate:
        return "NaN"
    return f"{100.0 * rate:.2f}%"


def print_table(title: str, rows: Sequence[Tuple[str, int, int, float]]) -> None:
    print(f"\n{title}")
    header = f"{'Scope':<12} {'Hit':>6} {'Total':>6} {'Acc':>12}"
    print(header)
    print("-" * len(header))
    for scope, hit, total, rate in rows:
        print(f"{scope:<12} {hit:>6} {total:>6} {fmt_pct(rate):>12}")


def evaluate(results_root: Path, gt_path: Path, use_combined_file_res: bool) -> None:
    gt_map = load_gt(gt_path)
    if not gt_map:
        raise RuntimeError("Ground truth is empty or invalid.")

    gt_ids_by_project: Dict[str, List[str]] = defaultdict(list)
    for iid, inst in gt_map.items():
        gt_ids_by_project[inst.project].append(iid)

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

    file_multi_hit5_by_project: Dict[str, int] = {p: 0 for p in all_projects}
    file_multi_den_by_project: Dict[str, int] = {p: 0 for p in all_projects}

    func_multi_hit10_by_project: Dict[str, int] = {p: 0 for p in all_projects}
    func_multi_hit15_by_project: Dict[str, int] = {p: 0 for p in all_projects}
    func_multi_den_by_project: Dict[str, int] = {p: 0 for p in all_projects}

    unknown_instance_counter = 0

    for project in all_projects:
        proj_dir = result_dirs[project]
        file_pred_path = proj_dir / (COMBINED_FILE_RES_PATH if use_combined_file_res else DEFAULT_FILE_RES_PATH)
        func_pred_path = proj_dir / RELATED_RES_PATH

        file_pred_map = load_file_predictions(file_pred_path)
        func_pred_map = load_function_predictions(func_pred_path)

        for iid in set(file_pred_map.keys()) | set(func_pred_map.keys()):
            if iid not in gt_map:
                unknown_instance_counter += 1

        for iid in gt_ids_by_project[project]:
            gt_inst = gt_map[iid]
            pred_files = file_pred_map.get(iid, [])
            pred_funcs = func_pred_map.get(iid, [])

            if len(gt_inst.gt_files) >= 2:
                file_multi_den_by_project[project] += 1
                file_multi_hit5_by_project[project] += compute_acc_at_k(pred_files, gt_inst.gt_files, 5)

            if len(gt_inst.gt_functions) >= 2:
                func_multi_den_by_project[project] += 1
                func_multi_hit10_by_project[project] += compute_acc_at_k(pred_funcs, gt_inst.gt_functions, 10)
                func_multi_hit15_by_project[project] += compute_acc_at_k(pred_funcs, gt_inst.gt_functions, 15)

    if unknown_instance_counter > 0:
        warn(f"{unknown_instance_counter} predicted instance_id(s) were not found in GT and skipped.")

    file_overall_hit = sum(file_multi_hit5_by_project.values())
    file_overall_den = sum(file_multi_den_by_project.values())
    file_rows: List[Tuple[str, int, int, float]] = [
        ("overall", file_overall_hit, file_overall_den, safe_rate(file_overall_hit, file_overall_den))
    ]
    file_rows.extend(
        (
            p,
            file_multi_hit5_by_project[p],
            file_multi_den_by_project[p],
            safe_rate(file_multi_hit5_by_project[p], file_multi_den_by_project[p]),
        )
        for p in all_projects
    )

    func10_overall_hit = sum(func_multi_hit10_by_project.values())
    func15_overall_hit = sum(func_multi_hit15_by_project.values())
    func_overall_den = sum(func_multi_den_by_project.values())

    func10_rows: List[Tuple[str, int, int, float]] = [
        ("overall", func10_overall_hit, func_overall_den, safe_rate(func10_overall_hit, func_overall_den))
    ]
    func10_rows.extend(
        (
            p,
            func_multi_hit10_by_project[p],
            func_multi_den_by_project[p],
            safe_rate(func_multi_hit10_by_project[p], func_multi_den_by_project[p]),
        )
        for p in all_projects
    )

    func15_rows: List[Tuple[str, int, int, float]] = [
        ("overall", func15_overall_hit, func_overall_den, safe_rate(func15_overall_hit, func_overall_den))
    ]
    func15_rows.extend(
        (
            p,
            func_multi_hit15_by_project[p],
            func_multi_den_by_project[p],
            safe_rate(func_multi_hit15_by_project[p], func_multi_den_by_project[p]),
        )
        for p in all_projects
    )

    print_table("File Multi-Target Acc@5", file_rows)
    print_table("Function Multi-Target Acc@10", func10_rows)
    print_table("Function Multi-Target Acc@15", func15_rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute multi-file/function Acc@k metrics and print to terminal.")
    parser.add_argument(
        "--results_root",
        type=str,
        default="results",
        help="Root folder that contains per-project result directories.",
    )
    parser.add_argument(
        "--gt_path",
        type=str,
        default="data/Def4CAE/filter.json",
        help="Ground-truth JSON path.",
    )
    parser.add_argument(
        "--use_combined_file_res",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use file_level_combined/combined_locs.jsonl for file predictions (default: true).",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    evaluate(
        results_root=Path(args.results_root),
        gt_path=Path(args.gt_path),
        use_combined_file_res=bool(args.use_combined_file_res),
    )


if __name__ == "__main__":
    main()
