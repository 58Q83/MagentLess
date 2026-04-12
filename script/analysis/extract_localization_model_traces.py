#!/usr/bin/env python3
"""Extract model output/reasoning traces and append contains-GT evaluation details."""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


API_RESPONSE_BLOCK_RE = re.compile(
    r"API response ChatCompletion\((.*?)(?=\n\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d{3}\s+-\s+\w+\s+-|\Z)",
    re.DOTALL,
)

CONTENT_RE = re.compile(
    r"message=ChatCompletionMessage\(content=(?P<q>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")",
    re.DOTALL,
)

REASONING_TEXT_RE = re.compile(
    r"'text':\s*(?P<q>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")",
    re.DOTALL,
)


@dataclass
class EvalInfo:
    instance_id: str
    file_contains_gt: int
    function_contains_gt: int
    pred_files: list[str]
    gt_files: list[str]
    pred_functions: list[tuple[str, str]]
    gt_functions: list[tuple[str, str]]


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def normalize_method_name(name: str) -> str:
    s = re.sub(r"\s+", "", name.strip())
    if "(" in s:
        s = s.split("(", 1)[0]
    if "::" in s:
        s = s.split("::")[-1]
    return s


def dedupe_keep_order(items: Iterable) -> list:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def parse_found_files(value) -> list[str]:
    files: list[str] = []
    if value is None:
        return files
    candidates = value if isinstance(value, list) else str(value).splitlines()
    for item in candidates:
        s = str(item).strip()
        if not s or s == "```":
            continue
        files.append(normalize_path(s))
    return dedupe_keep_order(files)


def parse_function_entries_from_chunk(chunk: str, file_path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    fpath = normalize_path(file_path)
    for raw_line in str(chunk).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("function:"):
            fn = normalize_method_name(line.split(":", 1)[1].strip())
            if fn:
                out.append((fpath, fn))
    return out


def parse_found_related_locs(value) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not isinstance(value, dict):
        return pairs
    for file_path, entries in value.items():
        if entries is None:
            continue
        chunks = entries if isinstance(entries, list) else [entries]
        for chunk in chunks:
            pairs.extend(parse_function_entries_from_chunk(str(chunk), str(file_path)))
    return dedupe_keep_order(pairs)


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def load_file_predictions(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in load_jsonl(path):
        iid = row.get("instance_id")
        if not iid:
            continue
        out[iid] = parse_found_files(row.get("found_files"))
    return out


def load_function_predictions(path: Path) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for row in load_jsonl(path):
        iid = row.get("instance_id")
        if not iid:
            continue
        out[iid] = parse_found_related_locs(row.get("found_related_locs"))
    return out


def load_gt(gt_path: Path) -> dict[str, tuple[set[str], set[tuple[str, str]]]]:
    with gt_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[str, tuple[set[str], set[tuple[str, str]]]] = {}
    for row in raw:
        iid = row.get("id")
        if not iid:
            continue
        gt_methods = row.get("ground_truth_methods") or []
        if not isinstance(gt_methods, list):
            gt_methods = []
        gt_files: set[str] = set()
        gt_funcs: set[tuple[str, str]] = set()
        for m in gt_methods:
            if not isinstance(m, dict):
                continue
            fpath = normalize_path(str(m.get("file", "")).strip())
            mname = normalize_method_name(str(m.get("method_name", "")).strip())
            if fpath:
                gt_files.add(fpath)
            if fpath and mname:
                gt_funcs.add((fpath, mname))
        out[iid] = (gt_files, gt_funcs)
    return out


def build_eval_info_map(results_root: Path, gt_path: Path) -> dict[str, EvalInfo]:
    gt_map = load_gt(gt_path)
    pred_files_map: dict[str, list[str]] = {}
    pred_funcs_map: dict[str, list[tuple[str, str]]] = {}

    for dataset_dir in sorted(results_root.iterdir()):
        if not dataset_dir.is_dir():
            continue
        file_jsonl = dataset_dir / "file_level" / "loc_outputs.jsonl"
        func_jsonl = dataset_dir / "related_elements" / "loc_outputs.jsonl"
        if file_jsonl.exists():
            pred_files_map.update(load_file_predictions(file_jsonl))
        if func_jsonl.exists():
            pred_funcs_map.update(load_function_predictions(func_jsonl))

    out: dict[str, EvalInfo] = {}
    for iid, (gt_files, gt_funcs) in gt_map.items():
        pred_files = pred_files_map.get(iid, [])
        pred_funcs = pred_funcs_map.get(iid, [])
        file_contains = int(gt_files.issubset(set(pred_files)))
        func_contains = int(gt_funcs.issubset(set(pred_funcs)))
        out[iid] = EvalInfo(
            instance_id=iid,
            file_contains_gt=file_contains,
            function_contains_gt=func_contains,
            pred_files=pred_files,
            gt_files=sorted(gt_files),
            pred_functions=pred_funcs,
            gt_functions=sorted(gt_funcs),
        )
    return out


def decode_py_string(literal: str) -> str:
    """Decode a Python-style string literal safely."""
    try:
        value = ast.literal_eval(literal)
        return value if isinstance(value, str) else str(value)
    except Exception:
        return literal.strip("'\"")


def extract_sections(log_text: str) -> list[tuple[str, str]]:
    """Return a list of (model_output, reasoning_text)."""
    sections: list[tuple[str, str]] = []

    for block_match in API_RESPONSE_BLOCK_RE.finditer(log_text):
        block = block_match.group(1)

        content_match = CONTENT_RE.search(block)
        model_output = decode_py_string(content_match.group("q")) if content_match else ""

        reasoning_texts = [
            decode_py_string(m.group("q")) for m in REASONING_TEXT_RE.finditer(block)
        ]
        reasoning_text = "\n\n".join([t for t in reasoning_texts if t.strip()])

        if model_output.strip() or reasoning_text.strip():
            sections.append((model_output, reasoning_text))

    return sections


def render_eval_info(eval_info: EvalInfo | None) -> list[str]:
    lines: list[str] = ["=== Contains GT Evaluation ===", ""]
    if eval_info is None:
        lines.append("No GT/evaluation data found for this instance.")
        lines.append("")
        return lines

    lines.append(f"[Contains GT] file={eval_info.file_contains_gt}, function={eval_info.function_contains_gt}")
    lines.append("")
    lines.append("[File Predictions]")
    if eval_info.pred_files:
        lines.extend(eval_info.pred_files)
    else:
        lines.append("(empty)")
    lines.append("")
    lines.append("[File GT]")
    if eval_info.gt_files:
        lines.extend(eval_info.gt_files)
    else:
        lines.append("(empty)")
    lines.append("")
    lines.append("[Function Predictions]")
    if eval_info.pred_functions:
        lines.extend([f"{f} :: {fn}" for f, fn in eval_info.pred_functions])
    else:
        lines.append("(empty)")
    lines.append("")
    lines.append("[Function GT]")
    if eval_info.gt_functions:
        lines.extend([f"{f} :: {fn}" for f, fn in eval_info.gt_functions])
    else:
        lines.append("(empty)")
    lines.append("")
    return lines


def render_readable(
    sections: list[tuple[str, str]],
    source_log: Path,
    eval_info: EvalInfo | None,
) -> str:
    lines: list[str] = [f"Source Log: {source_log}", ""]
    lines.extend(render_eval_info(eval_info))

    for idx, (model_output, reasoning_text) in enumerate(sections, 1):
        lines.append(f"=== Response {idx} ===")
        lines.append("")
        lines.append("[Model Output]")
        lines.append(model_output.strip() or "(empty)")
        lines.append("")
        lines.append("[Thinking]")
        lines.append(reasoning_text.strip() or "(empty)")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def process_log(
    log_path: Path,
    *,
    results_root: Path,
    unified_output_root: Path | None,
    out_root_name: str,
    eval_info_map: dict[str, EvalInfo],
) -> tuple[bool, Path | None]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    sections = extract_sections(text)
    if not sections:
        return False, None

    # Unified mode:
    # results/<dataset>/<stage>/localization_logs/<id>.log
    # -> <unified_output_root>/<dataset>/<stage>/<id>.log
    if unified_output_root is not None:
        rel = log_path.relative_to(results_root)
        # rel parts: <dataset>, <stage>, localization_logs, <file>
        dataset = rel.parts[0]
        stage = rel.parts[1]
        out_dir = unified_output_root / dataset / stage
    else:
        # Per-stage mode:
        # .../<stage>/localization_logs/<instanceId>.log
        # -> .../<stage>/<out_root_name>/<instanceId>.log
        stage_dir = log_path.parent.parent
        out_dir = stage_dir / out_root_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / log_path.name
    instance_id = log_path.stem
    eval_info = eval_info_map.get(instance_id)
    out_path.write_text(render_readable(sections, log_path, eval_info), encoding="utf-8")
    return True, out_path


def iter_localization_logs(results_root: Path) -> list[Path]:
    return sorted(
        p
        for p in results_root.rglob("*.log")
        if p.parent.name == "localization_logs"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract model output and reasoning from localization logs into readable files."
        )
    )
    parser.add_argument(
        "--results-root",
        default="results",
        help="Root directory containing *_dataset.*/(file_level|related_elements)/localization_logs",
    )
    parser.add_argument(
        "--out-dir-name",
        default="localization_logs_readable",
        help="Output directory name under each stage directory (used when --output-root is not set)",
    )
    parser.add_argument(
        "--output-root",
        default="results/localization_logs_readable_all",
        help=(
            "Unified output root directory. "
            "If set, outputs are written to <output-root>/<dataset>/<stage>/<instanceId>.log"
        ),
    )
    parser.add_argument(
        "--gt-path",
        default="data/Def4CAE/filter.json",
        help="Ground truth JSON used to compute contains-gt details.",
    )
    args = parser.parse_args()

    results_root = Path(args.results_root).resolve()
    output_root = Path(args.output_root).resolve() if args.output_root else None
    gt_path = Path(args.gt_path).resolve()
    logs = iter_localization_logs(results_root)
    eval_info_map = build_eval_info_map(results_root, gt_path)

    extracted = 0
    skipped = 0
    for log_path in logs:
        ok, _ = process_log(
            log_path,
            results_root=results_root,
            unified_output_root=output_root,
            out_root_name=args.out_dir_name,
            eval_info_map=eval_info_map,
        )
        if ok:
            extracted += 1
        else:
            skipped += 1

    print(f"Scanned logs: {len(logs)}")
    print(f"Extracted: {extracted}")
    print(f"Skipped (no API response section found): {skipped}")
    if output_root is not None:
        print(f"Unified output root: {output_root}")
    else:
        print(f"Output dir name: {args.out_dir_name}")


if __name__ == "__main__":
    main()
