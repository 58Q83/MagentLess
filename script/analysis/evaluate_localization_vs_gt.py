#!/usr/bin/env python3
"""Evaluate defect localization outputs against Def4CAE ground truth.

Evaluation protocol:
1) File-level single-file instances: Hit@Top-K (K from --topk)
2) File-level multi-file instances: Coverage@K where K=--file_multi_topk
3) Function-level single-function instances: Hit@Top-K (K from --topk)
4) Function-level all instances: Coverage@K_i where K_i=ceil(factor * |GT_functions|)

Missing predictions are included and scored as 0.

Outputs:
- metrics_file_single_overall.csv
- metrics_file_single_by_project.csv
- metrics_function_single_overall.csv
- metrics_function_single_by_project.csv
- instance_level_detailed_metrics.csv
- hist_file_multi_coverage_values.csv
- hist_function_all_coverage_values.csv
- figure_file_multi_coverage_hist_overall.(png|pdf)
- figure_file_multi_coverage_hist_by_project.(png|pdf)
- figure_function_all_coverage_hist_overall.(png|pdf)
- figure_function_all_coverage_hist_by_project.(png|pdf)
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


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def normalize_method_name(name: str) -> str:
    s = re.sub(r"\s+", "", name.strip())
    # Drop parameter list so "foo(int)" and "foo" can match.
    if "(" in s:
        s = s.split("(", 1)[0]
    # Keep only unqualified basename so "A::B::foo" -> "foo".
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


def compute_coverage(pred_list: Sequence, gt_set: Set, k: int) -> float:
    if not gt_set:
        return float("nan")
    hit_count = len(set(pred_list[:k]) & gt_set)
    return hit_count / len(gt_set)


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


def add_hist_count_labels(ax, counts, patches) -> None:
    ymax = max([float(c) for c in counts], default=0.0)
    ypad = max(0.2, 0.02 * ymax)
    for c, patch in zip(counts, patches):
        h = float(c)
        if h <= 0:
            continue
        x = patch.get_x() + patch.get_width() / 2.0
        ax.annotate(
            f"{int(round(h))}",
            xy=(x, h),
            xytext=(0, 2),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )
    if ymax > 0:
        ax.set_ylim(0, ymax + ypad)


def write_csv(path: Path, rows: List[dict], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def preferred_project_order(name: str) -> Tuple[int, str]:
    preferred = {"OCCT": 0, "OpenFOAM": 1, "mfem": 2, "dealii": 3}
    return (preferred.get(name, 99), name)


def save_figure_split_formats(fig, output_dir: Path, filename_base: str) -> None:
    png_dir = output_dir / "png"
    pdf_dir = output_dir / "pdf"
    png_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_dir / f"{filename_base}.png", bbox_inches="tight")
    fig.savefig(pdf_dir / f"{filename_base}.pdf", bbox_inches="tight")


def plot_hit_by_project(
    output_dir: Path,
    filename_base: str,
    title: str,
    topk: Sequence[int],
    rows: List[dict],
) -> None:
    if not rows:
        warn(f"No rows for {filename_base}, skip plotting.")
        return

    rows = sorted(rows, key=lambda r: preferred_project_order(r["project"]))
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
    subtitle = " | ".join([f'{r["project"]}: N={r["evaluated_n"]}' for r in rows])
    ax.set_title(f"{title}\n({subtitle})", fontsize=11)
    ax.legend(frameon=True, ncol=len(topk))
    fig.tight_layout()

    save_figure_split_formats(fig, output_dir, filename_base)
    plt.close(fig)


def plot_coverage_hist_overall(
    output_dir: Path,
    filename_base: str,
    title: str,
    values: List[float],
    bins: int,
) -> None:
    if not values:
        warn(f"No values for {filename_base}, skip plotting.")
        return

    set_plot_style()
    fig, ax = plt.subplots(figsize=(8.4, 5.2), dpi=300)
    counts, _, patches = ax.hist(values, bins=bins, range=(0.0, 1.0), edgecolor="black", alpha=0.85)
    add_hist_count_labels(ax, counts, patches)
    ax.set_xlabel("Coverage")
    ax.set_ylabel("Instance Count")
    ax.set_title(f"{title}\n(N={len(values)})")
    ax.set_xlim(0, 1)
    fig.tight_layout()

    save_figure_split_formats(fig, output_dir, filename_base)
    plt.close(fig)


def plot_coverage_hist_by_project(
    output_dir: Path,
    filename_base: str,
    title: str,
    values_by_project: Dict[str, List[float]],
    bins: int,
) -> None:
    projects = sorted(values_by_project.keys(), key=preferred_project_order)
    if not projects:
        warn(f"No project values for {filename_base}, skip plotting.")
        return

    set_plot_style()
    n = len(projects)
    ncols = min(3, n)
    nrows = (n + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows=nrows, ncols=ncols, figsize=(5.6 * ncols, 3.8 * nrows), dpi=300)

    if isinstance(axes, plt.Axes):
        axes_list = [axes]
    else:
        axes_list = list(axes.ravel())

    for ax, project in zip(axes_list, projects):
        vals = values_by_project.get(project, [])
        counts, _, patches = ax.hist(
            vals,
            bins=bins,
            range=(0.0, 1.0),
            edgecolor="black",
            alpha=0.85,
        )
        add_hist_count_labels(ax, counts, patches)
        ax.set_title(f"{project} (N={len(vals)})")
        ax.set_xlim(0, 1)
        ax.set_xlabel("Coverage")
        ax.set_ylabel("Instance Count")

    for ax in axes_list[len(projects):]:
        ax.axis("off")

    fig.suptitle(title, fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    save_figure_split_formats(fig, output_dir, filename_base)
    plt.close(fig)


def evaluate(args: argparse.Namespace) -> None:
    results_root = Path(args.results_root)
    gt_path = Path(args.gt_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    topk = parse_topk(args.topk)
    file_multi_topk = int(args.file_multi_topk)
    if file_multi_topk <= 0:
        raise ValueError("--file_multi_topk must be positive")
    function_coverage_factor = float(args.function_coverage_factor)
    if function_coverage_factor <= 0:
        raise ValueError("--function_coverage_factor must be positive")
    coverage_hist_bins = int(args.coverage_hist_bins)
    if coverage_hist_bins <= 0:
        raise ValueError("--coverage_hist_bins must be positive")

    info(f"Hit Top-k = {topk}")
    info(f"File multi coverage K = {file_multi_topk}")
    info(f"Function coverage factor = {function_coverage_factor}")
    info(f"Coverage histogram bins = {coverage_hist_bins}")

    gt_map = load_gt(gt_path)
    if not gt_map:
        raise RuntimeError("Ground truth is empty or invalid.")

    gt_ids_by_project: Dict[str, List[str]] = defaultdict(list)
    for iid, inst in gt_map.items():
        gt_ids_by_project[inst.project].append(iid)

    result_dirs = discover_project_result_dirs(results_root)
    if not result_dirs:
        raise RuntimeError(f"No valid result project directory found under: {results_root}")

    all_projects = sorted(set(result_dirs.keys()) & set(gt_ids_by_project.keys()), key=preferred_project_order)
    if not all_projects:
        raise RuntimeError("No overlap between result projects and GT projects.")

    info(f"Projects for evaluation: {', '.join(all_projects)}")

    # hit tables
    file_single_by_project_hits: Dict[str, Dict[int, int]] = {p: {k: 0 for k in topk} for p in all_projects}
    file_single_by_project_den: Dict[str, int] = {p: 0 for p in all_projects}

    func_single_by_project_hits: Dict[str, Dict[int, int]] = {p: {k: 0 for k in topk} for p in all_projects}
    func_single_by_project_den: Dict[str, int] = {p: 0 for p in all_projects}

    # coverage value lists for histograms
    file_multi_coverage_rows: List[dict] = []
    func_all_coverage_rows: List[dict] = []

    instance_rows: List[dict] = []

    unknown_instance_counter = 0

    for project in all_projects:
        proj_dir = result_dirs[project]
        file_pred_map = load_file_predictions(proj_dir / "file_level" / "loc_outputs.jsonl")
        func_pred_map = load_function_predictions(proj_dir / "related_elements" / "loc_outputs.jsonl")

        for iid in set(file_pred_map.keys()) | set(func_pred_map.keys()):
            if iid not in gt_map:
                unknown_instance_counter += 1

        project_gt_ids = sorted(gt_ids_by_project[project])

        for iid in project_gt_ids:
            gt_inst = gt_map[iid]
            pred_files = file_pred_map.get(iid, [])
            pred_funcs = func_pred_map.get(iid, [])

            gt_file_count = len(gt_inst.gt_files)
            gt_func_count = len(gt_inst.gt_functions)

            is_file_single = int(gt_file_count == 1)
            is_file_multi = int(gt_file_count >= 2)
            is_function_single = int(gt_func_count == 1)
            is_function_multi = int(gt_func_count >= 2)

            file_hit_map: Dict[int, Optional[int]] = {k: None for k in topk}
            func_hit_map: Dict[int, Optional[int]] = {k: None for k in topk}

            file_multi_coverage = None
            func_all_k = None
            func_all_coverage = None

            # File single-hit metrics (include missing predictions as 0 by using default []).
            if gt_file_count == 1:
                hits = compute_hits_for_topk(pred_files, gt_inst.gt_files, topk)
                file_single_by_project_den[project] += 1
                for k in topk:
                    file_hit_map[k] = hits[k]
                    file_single_by_project_hits[project][k] += hits[k]

            # File multi coverage@fixed K.
            if gt_file_count >= 2:
                file_multi_coverage = compute_coverage(pred_files, gt_inst.gt_files, file_multi_topk)
                file_multi_coverage_rows.append(
                    {
                        "project": project,
                        "instance_id": iid,
                        "gt_count": gt_file_count,
                        "k": file_multi_topk,
                        "pred_count": len(pred_files),
                        "coverage": file_multi_coverage,
                    }
                )

            # Function single-hit metrics.
            if gt_func_count == 1:
                hits = compute_hits_for_topk(pred_funcs, gt_inst.gt_functions, topk)
                func_single_by_project_den[project] += 1
                for k in topk:
                    func_hit_map[k] = hits[k]
                    func_single_by_project_hits[project][k] += hits[k]

            # Function multi-target coverage@dynamic K_i.
            # Single-function instances are excluded by design.
            if gt_func_count >= 2:
                func_all_k = int(math.ceil(function_coverage_factor * gt_func_count))
                func_all_coverage = compute_coverage(pred_funcs, gt_inst.gt_functions, func_all_k)
                func_all_coverage_rows.append(
                    {
                        "project": project,
                        "instance_id": iid,
                        "gt_count": gt_func_count,
                        "k": func_all_k,
                        "pred_count": len(pred_funcs),
                        "coverage": func_all_coverage,
                        "is_function_single": is_function_single,
                        "is_function_multi": is_function_multi,
                    }
                )

            row = {
                "project": project,
                "instance_id": iid,
                "gt_file_count": gt_file_count,
                "gt_function_count": gt_func_count,
                "file_has_prediction": int(iid in file_pred_map),
                "function_has_prediction": int(iid in func_pred_map),
                "file_pred_count": len(pred_files),
                "function_pred_count": len(pred_funcs),
                "is_file_single": is_file_single,
                "is_file_multi": is_file_multi,
                "is_function_single": is_function_single,
                "is_function_multi": is_function_multi,
                f"file_multi_coverage@{file_multi_topk}": file_multi_coverage,
                "function_all_dynamic_k": func_all_k,
                "function_all_coverage@dynamic_k": func_all_coverage,
            }
            for k in topk:
                row[f"file_single_hit@{k}"] = file_hit_map[k]
                row[f"function_single_hit@{k}"] = func_hit_map[k]
            instance_rows.append(row)

    if unknown_instance_counter > 0:
        warn(f"{unknown_instance_counter} predicted instance_id(s) were not found in GT and skipped.")

    # Build file single hit tables.
    file_single_overall_row = {
        "scope": "overall",
        "evaluated_n": sum(file_single_by_project_den.values()),
    }
    for k in topk:
        num = sum(file_single_by_project_hits[p][k] for p in all_projects)
        den = file_single_overall_row["evaluated_n"]
        file_single_overall_row[f"hit@{k}"] = safe_rate(num, den)

    file_single_by_project_rows: List[dict] = []
    for project in all_projects:
        row = {
            "project": project,
            "evaluated_n": file_single_by_project_den[project],
        }
        for k in topk:
            row[f"hit@{k}"] = safe_rate(
                file_single_by_project_hits[project][k],
                file_single_by_project_den[project],
            )
        file_single_by_project_rows.append(row)

    # Build function single hit tables.
    func_single_overall_row = {
        "scope": "overall",
        "evaluated_n": sum(func_single_by_project_den.values()),
    }
    for k in topk:
        num = sum(func_single_by_project_hits[p][k] for p in all_projects)
        den = func_single_overall_row["evaluated_n"]
        func_single_overall_row[f"hit@{k}"] = safe_rate(num, den)

    func_single_by_project_rows: List[dict] = []
    for project in all_projects:
        row = {
            "project": project,
            "evaluated_n": func_single_by_project_den[project],
        }
        for k in topk:
            row[f"hit@{k}"] = safe_rate(
                func_single_by_project_hits[project][k],
                func_single_by_project_den[project],
            )
        func_single_by_project_rows.append(row)

    # Monotonicity checks for hit tables.
    for label, row in (
        ("file_single_overall", file_single_overall_row),
        ("function_single_overall", func_single_overall_row),
    ):
        rates = [row[f"hit@{k}"] for k in topk]
        for i in range(len(rates) - 1):
            a = rates[i]
            b = rates[i + 1]
            if not (math.isnan(a) or math.isnan(b) or a <= b + 1e-12):
                warn(f"Monotonicity check failed for {label}: Top-{topk[i]} > Top-{topk[i+1]}")

    # Save CSVs.
    topk_fields = [f"hit@{k}" for k in topk]
    write_csv(
        output_dir / "metrics_file_single_overall.csv",
        [file_single_overall_row],
        ["scope", "evaluated_n"] + topk_fields,
    )
    write_csv(
        output_dir / "metrics_file_single_by_project.csv",
        file_single_by_project_rows,
        ["project", "evaluated_n"] + topk_fields,
    )

    write_csv(
        output_dir / "metrics_function_single_overall.csv",
        [func_single_overall_row],
        ["scope", "evaluated_n"] + topk_fields,
    )
    write_csv(
        output_dir / "metrics_function_single_by_project.csv",
        func_single_by_project_rows,
        ["project", "evaluated_n"] + topk_fields,
    )

    instance_fields = [
        "project",
        "instance_id",
        "gt_file_count",
        "gt_function_count",
        "file_has_prediction",
        "function_has_prediction",
        "file_pred_count",
        "function_pred_count",
        "is_file_single",
        "is_file_multi",
        "is_function_single",
        "is_function_multi",
    ] + [
        f"file_single_hit@{k}" for k in topk
    ] + [
        f"function_single_hit@{k}" for k in topk
    ] + [
        f"file_multi_coverage@{file_multi_topk}",
        "function_all_dynamic_k",
        "function_all_coverage@dynamic_k",
    ]
    write_csv(output_dir / "instance_level_detailed_metrics.csv", instance_rows, instance_fields)

    write_csv(
        output_dir / "hist_file_multi_coverage_values.csv",
        file_multi_coverage_rows,
        ["project", "instance_id", "gt_count", "k", "pred_count", "coverage"],
    )
    write_csv(
        output_dir / "hist_function_all_coverage_values.csv",
        func_all_coverage_rows,
        [
            "project",
            "instance_id",
            "gt_count",
            "k",
            "pred_count",
            "coverage",
            "is_function_single",
            "is_function_multi",
        ],
    )

    # Plot hit-rate by project for single-file/single-function.
    plot_hit_by_project(
        output_dir=output_dir,
        filename_base="figure_file_single_hit_by_project",
        title="Project-Wise File Single-Target Hit Rate",
        topk=topk,
        rows=file_single_by_project_rows,
    )
    plot_hit_by_project(
        output_dir=output_dir,
        filename_base="figure_function_single_hit_by_project",
        title="Project-Wise Function Single-Target Hit Rate",
        topk=topk,
        rows=func_single_by_project_rows,
    )

    # Coverage histograms.
    file_multi_values = [float(r["coverage"]) for r in file_multi_coverage_rows if not math.isnan(float(r["coverage"]))]
    func_all_values = [float(r["coverage"]) for r in func_all_coverage_rows if not math.isnan(float(r["coverage"]))]

    file_multi_by_project: Dict[str, List[float]] = defaultdict(list)
    for r in file_multi_coverage_rows:
        cov = float(r["coverage"])
        if not math.isnan(cov):
            file_multi_by_project[r["project"]].append(cov)

    func_all_by_project: Dict[str, List[float]] = defaultdict(list)
    for r in func_all_coverage_rows:
        cov = float(r["coverage"])
        if not math.isnan(cov):
            func_all_by_project[r["project"]].append(cov)

    plot_coverage_hist_overall(
        output_dir=output_dir,
        filename_base="figure_file_multi_coverage_hist_overall",
        title=f"File Multi-Target Coverage@{file_multi_topk} Distribution",
        values=file_multi_values,
        bins=coverage_hist_bins,
    )
    plot_coverage_hist_by_project(
        output_dir=output_dir,
        filename_base="figure_file_multi_coverage_hist_by_project",
        title=f"File Multi-Target Coverage@{file_multi_topk} Distribution by Project",
        values_by_project=file_multi_by_project,
        bins=coverage_hist_bins,
    )

    plot_coverage_hist_overall(
        output_dir=output_dir,
        filename_base="figure_function_all_coverage_hist_overall",
        title="Function Coverage@dynamic_k Distribution",
        values=func_all_values,
        bins=coverage_hist_bins,
    )
    plot_coverage_hist_by_project(
        output_dir=output_dir,
        filename_base="figure_function_all_coverage_hist_by_project",
        title="Function Coverage@dynamic_k Distribution by Project",
        values_by_project=func_all_by_project,
        bins=coverage_hist_bins,
    )

    # Save a compact run-summary JSON for traceability.
    with (output_dir / "metrics_summary.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "topk": list(topk),
                "file_multi_topk": file_multi_topk,
                "function_coverage_factor": function_coverage_factor,
                "coverage_hist_bins": coverage_hist_bins,
                "projects": all_projects,
                "file_single_overall": file_single_overall_row,
                "function_single_overall": func_single_overall_row,
                "file_multi_hist_n": len(file_multi_values),
                "function_all_hist_n": len(func_all_values),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

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
        help="Top-k values for hit-rate metrics, e.g. --topk 1 3 5",
    )
    parser.add_argument(
        "--file_multi_topk",
        type=int,
        default=5,
        help="K used for file multi-target coverage@K.",
    )
    parser.add_argument(
        "--function_coverage_factor",
        type=float,
        default=1.5,
        help="Dynamic-k factor for function coverage: K_i=ceil(factor * |GT_i|).",
    )
    parser.add_argument(
        "--coverage_hist_bins",
        type=int,
        default=10,
        help="Number of bins for coverage histograms in [0, 1].",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    evaluate(args)


if __name__ == "__main__":
    main()
