#!/usr/bin/env python3
"""Export function-level / class-level related-elements predictions vs GT by instance.

Notes:
- Function-level matching follows evaluate_localization_vs_gt.py style: compare (file, normalized_function_name).
- Class-level GT is heuristic because GT schema has method-level labels only.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_TOPK = (1, 3, 5)


def normalize_path(path: str) -> str:
    return str(path).strip().replace("\\", "/")


def normalize_method_name(name: str) -> str:
    s = re.sub(r"\s+", "", str(name).strip())
    if "::" in s:
        s = s.split("::")[-1]
    return s


def normalize_class_name(name: str) -> str:
    s = re.sub(r"\s+", "", str(name).strip())
    # remove template args for broader matching, e.g. Foo<T> -> Foo
    s = re.sub(r"<.*>$", "", s)
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


def parse_related_entries_from_chunk(chunk: str, file_path: str) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    funcs: List[Tuple[str, str]] = []
    classes: List[Tuple[str, str]] = []
    fpath = normalize_path(file_path)

    for raw_line in str(chunk).splitlines():
        line = raw_line.strip()
        if not line:
            continue

        lower = line.lower()
        if lower.startswith("function:"):
            fn_raw = line.split(":", 1)[1].strip()
            fn = normalize_method_name(fn_raw)
            if fn:
                funcs.append((fpath, fn))
        elif lower.startswith("class:"):
            cls_raw = line.split(":", 1)[1].strip()
            cls = normalize_class_name(cls_raw)
            if cls:
                classes.append((fpath, cls))

    return funcs, classes


def parse_found_related_locs(value) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    funcs: List[Tuple[str, str]] = []
    classes: List[Tuple[str, str]] = []
    if not isinstance(value, dict):
        return funcs, classes

    for file_path, entries in value.items():
        if entries is None:
            continue
        chunks = entries if isinstance(entries, list) else [entries]
        for chunk in chunks:
            f_part, c_part = parse_related_entries_from_chunk(str(chunk), str(file_path))
            funcs.extend(f_part)
            classes.extend(c_part)

    return dedupe_keep_order(funcs), dedupe_keep_order(classes)


def load_related_predictions(path: Path, project_prefix: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not path.exists():
        return out

    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON decode failed at {path}:{i}: {exc}") from exc

            iid = str(row.get("instance_id", ""))
            if not iid.startswith(project_prefix):
                continue

            pred_funcs, pred_classes = parse_found_related_locs(row.get("found_related_locs"))
            out[iid] = {
                "pred_functions": pred_funcs,
                "pred_classes": pred_classes,
                "found_related_locs_raw": row.get("found_related_locs"),
            }
    return out


def infer_gt_class_candidates(method_name: str) -> List[str]:
    raw = str(method_name).strip()
    if not raw:
        return []

    out: List[str] = []

    # case 1: scoped style, e.g. Foo::bar / ns::Foo::bar
    if "::" in raw:
        parts = [p for p in raw.split("::") if p]
        if len(parts) >= 2:
            out.append(normalize_class_name(parts[-2]))

    # case 2: method name itself might be class/struct type identifier
    # heuristic: starts with uppercase letter
    compact = re.sub(r"\s+", "", raw)
    if compact and compact[0].isupper():
        out.append(normalize_class_name(compact))

    return dedupe_keep_order([x for x in out if x])


def load_gt(gt_path: Path, project_prefix: str) -> Dict[str, dict]:
    raw = json.loads(gt_path.read_text(encoding="utf-8"))
    out: Dict[str, dict] = {}

    for row in raw:
        iid = str(row.get("id", ""))
        if not iid.startswith(project_prefix):
            continue

        gt_methods = row.get("ground_truth_methods") or []
        gt_method_rows = []
        gt_func_pairs: List[Tuple[str, str]] = []
        gt_class_pairs: List[Tuple[str, str]] = []

        if isinstance(gt_methods, list):
            for m in gt_methods:
                if not isinstance(m, dict):
                    continue
                file_path = normalize_path(m.get("file", ""))
                method_name_raw = str(m.get("method_name", "")).strip()
                signature = m.get("signature")

                gt_method_rows.append(
                    {
                        "file": file_path,
                        "method_name": method_name_raw,
                        "signature": signature,
                    }
                )

                fn = normalize_method_name(method_name_raw)
                if file_path and fn:
                    gt_func_pairs.append((file_path, fn))

                for cls in infer_gt_class_candidates(method_name_raw):
                    if file_path and cls:
                        gt_class_pairs.append((file_path, cls))

        out[iid] = {
            "gt_methods_raw": gt_method_rows,
            "gt_functions": dedupe_keep_order(gt_func_pairs),
            "gt_classes_heuristic": dedupe_keep_order(gt_class_pairs),
        }

    return out


def compute_hits_for_topk(pred_list: Sequence, gt_set: Set, topk: Sequence[int]) -> Dict[int, int]:
    res: Dict[int, int] = {}
    for k in topk:
        pred_cut = pred_list[:k]
        res[k] = int(any(item in gt_set for item in pred_cut))
    return res


def compute_recall_for_topk(pred_list: Sequence, gt_set: Set, topk: Sequence[int]) -> Dict[int, Optional[float]]:
    res: Dict[int, Optional[float]] = {}
    if not gt_set:
        for k in topk:
            res[k] = None
        return res

    for k in topk:
        pred_cut = pred_list[:k]
        covered = len(set(pred_cut) & gt_set)
        res[k] = covered / len(gt_set)
    return res


def first_hit_rank(pred_list: Sequence, gt_set: Set) -> Optional[int]:
    for i, item in enumerate(pred_list, start=1):
        if item in gt_set:
            return i
    return None


def build_level_block(pred_list: List[Tuple[str, str]], gt_list: List[Tuple[str, str]], topk: Sequence[int]) -> dict:
    gt_set = set(gt_list)
    pred_set = set(pred_list)

    hit_map = compute_hits_for_topk(pred_list, gt_set, topk)
    recall_map = compute_recall_for_topk(pred_list, gt_set, topk)

    intersection = [x for x in pred_list if x in gt_set]
    missing = [x for x in gt_list if x not in pred_set]
    extra = [x for x in pred_list if x not in gt_set]

    metrics = {}
    for k in topk:
        metrics[f"hit@{k}"] = hit_map[k]
        metrics[f"recall@{k}"] = recall_map[k]

    return {
        "gt_count": len(gt_list),
        "pred_count": len(pred_list),
        "gt": [{"file": f, "name": n} for (f, n) in gt_list],
        "pred": [{"file": f, "name": n} for (f, n) in pred_list],
        "intersection_in_pred_order": [{"file": f, "name": n} for (f, n) in intersection],
        "missing_gt": [{"file": f, "name": n} for (f, n) in missing],
        "extra_pred": [{"file": f, "name": n} for (f, n) in extra],
        "first_hit_rank": first_hit_rank(pred_list, gt_set),
        "metrics": metrics,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export related-elements function/class predictions vs GT by instance."
    )
    parser.add_argument(
        "--gt_path",
        type=str,
        default="data/Def4CAE/Def4CAE_26.2.28.json",
        help="Path to GT JSON.",
    )
    parser.add_argument(
        "--pred_jsonl",
        type=str,
        default="results/dealii.gpt5.4mini/related_elements/loc_outputs.jsonl",
        help="Path to related-elements prediction JSONL.",
    )
    parser.add_argument(
        "--project_prefix",
        type=str,
        default="dealii-",
        help="Only include instance IDs with this prefix.",
    )
    parser.add_argument(
        "--topk",
        nargs="+",
        type=int,
        default=list(DEFAULT_TOPK),
        help="Top-k values used for quick metrics in the exported JSON.",
    )
    parser.add_argument(
        "--output_json",
        type=str,
        default="results/analysis/dealii_related_elements_vs_gt.json",
        help="Output JSON path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    topk = parse_topk(args.topk)

    gt_path = Path(args.gt_path)
    pred_path = Path(args.pred_jsonl)
    output_path = Path(args.output_json)

    gt_map = load_gt(gt_path, args.project_prefix)
    pred_map = load_related_predictions(pred_path, args.project_prefix)

    all_ids = sorted(set(gt_map.keys()) | set(pred_map.keys()))

    instances = []
    for iid in all_ids:
        gt_item = gt_map.get(iid, {
            "gt_methods_raw": [],
            "gt_functions": [],
            "gt_classes_heuristic": [],
        })
        pred_item = pred_map.get(iid, {
            "pred_functions": [],
            "pred_classes": [],
            "found_related_locs_raw": None,
        })

        function_block = build_level_block(
            pred_list=pred_item["pred_functions"],
            gt_list=gt_item["gt_functions"],
            topk=topk,
        )

        class_block = build_level_block(
            pred_list=pred_item["pred_classes"],
            gt_list=gt_item["gt_classes_heuristic"],
            topk=topk,
        )

        instances.append(
            {
                "instance_id": iid,
                "has_gt": iid in gt_map,
                "has_prediction": iid in pred_map,
                "gt_methods_raw": gt_item["gt_methods_raw"],
                "function_level": function_block,
                "class_level": class_block,
                "notes": {
                    "class_level_gt_is_heuristic": True,
                    "class_level_gt_rule": (
                        "From GT method_name: take scoped class if `::` exists; "
                        "also keep method_name itself when it starts with uppercase."
                    ),
                },
            }
        )

    payload = {
        "project_prefix": args.project_prefix,
        "gt_path": str(gt_path),
        "pred_jsonl": str(pred_path),
        "topk": topk,
        "num_instances": len(instances),
        "instances": instances,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] wrote {len(instances)} instance rows -> {output_path}")


if __name__ == "__main__":
    main()
