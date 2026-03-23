#!/usr/bin/env python3
"""Evaluate defect localization outputs against Def4CAE ground truth.

This script compares:
1) File-level localization (`found_files`)
2) Function-level localization (`found_related_locs`, function-only)

Metrics:
- Top-1 / Top-3 / Top-5 hit rate

Outputs:
- metrics_overall.csv / metrics_overall.json
- metrics_by_project.csv
- instance_level_hits.csv
- figure_overall_topk.(png|pdf)
- figure_project_file_level.(png|pdf)
- figure_project_function_level.(png|pdf)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import matplotlib.pyplot as plt


DEFAULT_TOPK = (1, 3, 5)


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
    return raw.strip()


def infer_project_from_instance_id(instance_id: str) -> str:
    iid = instance_id.lower()
    if iid.startswith("openfoam-dev-"):
        return "OpenFOAM"
    if iid.startswith("occt-"):
        return "OCCT"
    if iid.startswith("mfem-"):
        return "mfem"
    return "UNKNOWN"


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def normalize_method_name(name: str) -> str:
    s = re.sub(r"\s+", "", name.strip())
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


def parse_topk(values: Sequence[int]) -> List[int]:
    ks = sorted(set(int(v) for v in values if int(v) > 0))
    if not ks:
        raise ValueError("Top-k values must contain at least one positive integer.")
    return ks


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
        gt_files: Set[str] = set()
        gt_funcs: Set[Tuple[str, str]] = set()

        if not isinstance(gt_methods, list):
            warn(f"GT instance {instance_id}: 'ground_truth_methods' is not a list.")
            gt_methods = []

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
    if isinstance(value, list):
        candidates = value
    else:
        candidates = str(value).splitlines()
    for item in candidates:
        s = str(item).strip()
        if not s or s == "```":
            continue
        files.append(normalize_path(s))
    return dedupe_keep_order(files)


def parse_function_entries_from_chunk(chunk: str, file_path: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for raw_line in str(chunk).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("function:"):
            fn = line.split(":", 1)[1].strip()
            fn = normalize_method_name(fn)
            if fn:
                out.append((normalize_path(file_path), fn))
    return out


def parse_found_related_locs(value) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    if not isinstance(value, dict):
        return pairs
    for file_path, entries in value.items():
        if entries is None:
            continue
        if isinstance(entries, list):
            chunks = entries
        else:
            chunks = [entries]
        for chunk in chunks:
            pairs.extend(parse_function_entries_from_chunk(str(chunk), str(file_path)))
    return dedupe_keep_order(pairs)


def discover_project_result_dirs(results_root: Path) -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    if not results_root.exists():
        return mapping
    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        raw_name = child.name.split("_", 1)[0]
        project = normalize_project_name(raw_name)
        file_jsonl = child / "file_level" / "loc_outputs.jsonl"
        related_jsonl = child / "related_elements" / "loc_outputs.jsonl"
        if file_jsonl.exists() or related_jsonl.exists():
            mapping[project] = child
    return mapping


def load_file_predictions(path: Path) -> Dict[str, List[str]]:
    rows = load_jsonl(path)
    out: Dict[str, List[str]] = {}
    for row in rows:
        instance_id = row.get("instance_id")
        if not instance_id:
            warn(f"{path}: one row missing 'instance_id', skipped.")
            continue
        out[instance_id] = parse_found_files(row.get("found_files"))
    return out


def load_function_predictions(path: Path) -> Dict[str, List[Tuple[str, str]]]:
    rows = load_jsonl(path)
    out: Dict[str, List[Tuple[str, str]]] = {}
    for row in rows:
        instance_id = row.get("instance_id")
        if not instance_id:
            warn(f"{path}: one row missing 'instance_id', skipped.")
            continue
        out[instance_id] = parse_found_related_locs(row.get("found_related_locs"))
    return out


def compute_hits_for_topk(pred_list: Sequence, gt_set: Set, topk: Sequence[int]) -> Dict[int, int]:
    res: Dict[int, int] = {}
    for k in topk:
        pred_cut = pred_list[:k]
        res[k] = int(any(item in gt_set for item in pred_cut))
    return res


def safe_rate(num: int, den: int) -> float:
    if den <= 0:
        return float("nan")
    return num / den


def set_plot_style() -> None:
    for style in ("seaborn-v0_8-whitegrid", "seaborn-whitegrid", "ggplot"):
        try:
            plt.style.use(style)
            return
        except OSError:
            continue


def add_bar_labels(ax, bars) -> None:
    for bar in bars:
        h = bar.get_height()
        if math.isnan(h):
            continue
        ax.annotate(
            f"{h:.1f}",
            xy=(bar.get_x() + bar.get_width() / 2.0, h),
            xytext=(0, 2),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
        )


def plot_overall_topk(
    output_dir: Path,
    topk: Sequence[int],
    overall_rows: List[dict],
) -> None:
    level_map = {r["level"]: r for r in overall_rows}
    file_rates = [100.0 * float(level_map["file"][f"hit@{k}"]) for k in topk]
    func_rates = [100.0 * float(level_map["function"][f"hit@{k}"]) for k in topk]

    set_plot_style()
    fig, ax = plt.subplots(figsize=(8.4, 5.2), dpi=300)

    x = list(range(len(topk)))
    width = 0.35
    bars1 = ax.bar([i - width / 2 for i in x], file_rates, width=width, label="File-Level")
    bars2 = ax.bar([i + width / 2 for i in x], func_rates, width=width, label="Function-Level")
    add_bar_labels(ax, bars1)
    add_bar_labels(ax, bars2)

    ax.set_xticks(x)
    ax.set_xticklabels([f"Top-{k}" for k in topk])
    ax.set_ylim(0, 100)
    ax.set_ylabel("Hit Rate (%)")
    ax.set_title("Overall Defect Localization Hit Rate")
    ax.legend(frameon=True)
    fig.tight_layout()

    fig.savefig(output_dir / "figure_overall_topk.png", bbox_inches="tight")
    fig.savefig(output_dir / "figure_overall_topk.pdf", bbox_inches="tight")
    plt.close(fig)


def plot_project_level(
    output_dir: Path,
    topk: Sequence[int],
    by_project_rows: List[dict],
    level: str,
) -> None:
    assert level in {"file", "function"}
    rows = [r for r in by_project_rows if r["level"] == level]
    if not rows:
        warn(f"No rows found for level={level}, skip plotting.")
        return

    preferred_order = {"OCCT": 0, "OpenFOAM": 1, "mfem": 2}
    rows = sorted(rows, key=lambda r: (preferred_order.get(r["project"], 99), r["project"]))
    projects = [r["project"] for r in rows]
    x = list(range(len(projects)))

    set_plot_style()
    fig, ax = plt.subplots(figsize=(9.0, 5.6), dpi=300)

    width = 0.22 if len(topk) >= 3 else 0.28
    offsets = [((i - (len(topk) - 1) / 2.0) * width) for i in range(len(topk))]

    for offset, k in zip(offsets, topk):
        y = [100.0 * float(r[f"hit@{k}"]) for r in rows]
        bars = ax.bar([xi + offset for xi in x], y, width=width, label=f"Top-{k}")
        add_bar_labels(ax, bars)

    ax.set_xticks(x)
    ax.set_xticklabels(projects)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Hit Rate (%)")
    if level == "file":
        ax.set_title("Project-Wise File-Level Hit Rate")
    else:
        title = "Project-Wise Function-Level Hit Rate"
        n_text = " | ".join(
            [f'{r["project"]}: N={r["evaluated_n"]}/{r["total_gt_n"]}' for r in rows]
        )
        ax.set_title(f"{title}\n({n_text})", fontsize=11)
    ax.legend(frameon=True, ncol=len(topk))
    fig.tight_layout()

    fig.savefig(output_dir / f"figure_project_{level}_level.png", bbox_inches="tight")
    fig.savefig(output_dir / f"figure_project_{level}_level.pdf", bbox_inches="tight")
    plt.close(fig)


def write_csv(path: Path, rows: List[dict], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def evaluate(args: argparse.Namespace) -> None:
    results_root = Path(args.results_root)
    gt_path = Path(args.gt_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    topk = parse_topk(args.topk)
    denominator_mode = args.function_denominator
    info(f"Top-k = {topk}")
    info(f"Function denominator mode = {denominator_mode}")

    gt_map = load_gt(gt_path)
    if not gt_map:
        raise RuntimeError("Ground truth is empty or invalid.")

    gt_ids_by_project: Dict[str, List[str]] = defaultdict(list)
    for iid, inst in gt_map.items():
        gt_ids_by_project[inst.project].append(iid)

    result_dirs = discover_project_result_dirs(results_root)
    if not result_dirs:
        raise RuntimeError(f"No valid result project directory found under: {results_root}")

    all_projects = sorted(set(result_dirs.keys()) & set(gt_ids_by_project.keys()))
    if not all_projects:
        raise RuntimeError("No overlap between result projects and GT projects.")

    info(f"Projects for evaluation: {', '.join(all_projects)}")

    by_project_rows: List[dict] = []
    instance_rows: List[dict] = []

    # These hold counts for overall (micro average).
    overall_hits = {
        "file": {k: 0 for k in topk},
        "function": {k: 0 for k in topk},
    }
    overall_den = {"file": 0, "function": 0}

    unknown_instance_counter = 0

    for project in all_projects:
        proj_dir = result_dirs[project]
        file_pred_map = load_file_predictions(proj_dir / "file_level" / "loc_outputs.jsonl")
        func_pred_map = load_function_predictions(
            proj_dir / "related_elements" / "loc_outputs.jsonl"
        )

        # Warn instances in prediction not found in GT.
        for iid in set(file_pred_map.keys()) | set(func_pred_map.keys()):
            if iid not in gt_map:
                unknown_instance_counter += 1

        project_gt_ids = sorted(gt_ids_by_project[project])

        file_eval_ids = [iid for iid in project_gt_ids if iid in file_pred_map]
        if not file_eval_ids:
            warn(f"{project}: no file-level predictions matched GT.")

        if denominator_mode == "all":
            func_eval_ids = list(project_gt_ids)
        else:
            func_eval_ids = [iid for iid in project_gt_ids if iid in func_pred_map]
        if not func_eval_ids:
            warn(f"{project}: no function-level instances under denominator={denominator_mode}.")

        # Per-project aggregated counters.
        proj_file_hits = {k: 0 for k in topk}
        proj_func_hits = {k: 0 for k in topk}

        # Instance-level details.
        for iid in project_gt_ids:
            gt_inst = gt_map[iid]
            pred_files = file_pred_map.get(iid, [])
            pred_funcs = func_pred_map.get(iid, [])

            file_has_pred = iid in file_pred_map
            func_has_pred = iid in func_pred_map
            file_included = file_has_pred
            func_included = (iid in func_eval_ids)

            file_hit_map: Dict[int, Optional[int]] = {k: None for k in topk}
            func_hit_map: Dict[int, Optional[int]] = {k: None for k in topk}

            if file_included:
                hits = compute_hits_for_topk(pred_files, gt_inst.gt_files, topk)
                for k in topk:
                    file_hit_map[k] = hits[k]
                    proj_file_hits[k] += hits[k]
                    overall_hits["file"][k] += hits[k]
                overall_den["file"] += 1

            if func_included:
                hits = compute_hits_for_topk(pred_funcs, gt_inst.gt_functions, topk)
                for k in topk:
                    func_hit_map[k] = hits[k]
                    proj_func_hits[k] += hits[k]
                    overall_hits["function"][k] += hits[k]
                overall_den["function"] += 1

            row = {
                "project": project,
                "instance_id": iid,
                "file_has_prediction": int(file_has_pred),
                "function_has_prediction": int(func_has_pred),
                "file_included": int(file_included),
                "function_included": int(func_included),
                "file_pred_count": len(pred_files),
                "function_pred_count": len(pred_funcs),
            }
            for k in topk:
                row[f"file_hit@{k}"] = file_hit_map[k]
                row[f"function_hit@{k}"] = func_hit_map[k]
            instance_rows.append(row)

        # Project rows (file)
        file_row = {
            "project": project,
            "level": "file",
            "denominator_mode": "available",
            "evaluated_n": len(file_eval_ids),
            "total_gt_n": len(project_gt_ids),
        }
        for k in topk:
            file_row[f"hit@{k}"] = safe_rate(proj_file_hits[k], len(file_eval_ids))
        by_project_rows.append(file_row)

        # Project rows (function)
        func_row = {
            "project": project,
            "level": "function",
            "denominator_mode": denominator_mode,
            "evaluated_n": len(func_eval_ids),
            "total_gt_n": len(project_gt_ids),
        }
        for k in topk:
            func_row[f"hit@{k}"] = safe_rate(proj_func_hits[k], len(func_eval_ids))
        by_project_rows.append(func_row)

    if unknown_instance_counter > 0:
        warn(f"{unknown_instance_counter} predicted instance_id(s) were not found in GT and skipped.")

    # Overall metrics (micro average across evaluated instances).
    overall_rows: List[dict] = []
    for level in ("file", "function"):
        den = overall_den[level]
        row = {
            "level": level,
            "denominator_mode": ("available" if level == "file" else denominator_mode),
            "evaluated_n": den,
        }
        for k in topk:
            row[f"hit@{k}"] = safe_rate(overall_hits[level][k], den)
        overall_rows.append(row)

    # Monotonicity checks: Top-1 <= Top-3 <= Top-5 ...
    for row in overall_rows:
        rates = [row[f"hit@{k}"] for k in topk]
        for i in range(len(rates) - 1):
            a = rates[i]
            b = rates[i + 1]
            if not (math.isnan(a) or math.isnan(b) or a <= b + 1e-12):
                warn(f"Monotonicity check failed for overall {row['level']}: Top-{topk[i]} > Top-{topk[i+1]}")

    # Save CSV/JSON
    metric_fields = ["level", "denominator_mode", "evaluated_n"] + [f"hit@{k}" for k in topk]
    write_csv(output_dir / "metrics_overall.csv", overall_rows, metric_fields)

    with (output_dir / "metrics_overall.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "topk": list(topk),
                "function_denominator_mode": denominator_mode,
                "metrics_overall": overall_rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    by_project_fields = [
        "project",
        "level",
        "denominator_mode",
        "evaluated_n",
        "total_gt_n",
    ] + [f"hit@{k}" for k in topk]
    write_csv(output_dir / "metrics_by_project.csv", by_project_rows, by_project_fields)

    instance_fields = [
        "project",
        "instance_id",
        "file_has_prediction",
        "function_has_prediction",
        "file_included",
        "function_included",
        "file_pred_count",
        "function_pred_count",
    ] + [f"file_hit@{k}" for k in topk] + [f"function_hit@{k}" for k in topk]
    write_csv(output_dir / "instance_level_hits.csv", instance_rows, instance_fields)

    # Plots
    plot_overall_topk(output_dir, topk, overall_rows)
    plot_project_level(output_dir, topk, by_project_rows, level="file")
    plot_project_level(output_dir, topk, by_project_rows, level="function")

    info(f"Saved outputs to: {output_dir.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate defect localization results against Def4CAE ground truth."
    )
    parser.add_argument(
        "--results_root",
        type=str,
        default="results",
        help="Root folder that contains per-project result directories.",
    )
    parser.add_argument(
        "--gt_path",
        type=str,
        default="data/Def4CAE/Def4CAE_26.2.28.json",
        help="Ground-truth JSON path.",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="results/analysis/localization_eval",
        help="Directory to write evaluation tables and figures.",
    )
    parser.add_argument(
        "--topk",
        nargs="+",
        type=int,
        default=list(DEFAULT_TOPK),
        help="Top-k values to evaluate, e.g. --topk 1 3 5",
    )
    parser.add_argument(
        "--function_denominator",
        choices=("available", "all"),
        default="available",
        help=(
            "Function-level denominator mode. "
            "'available': evaluate only instances with function outputs. "
            "'all': evaluate all GT instances and treat missing outputs as miss."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    evaluate(args)


if __name__ == "__main__":
    main()
