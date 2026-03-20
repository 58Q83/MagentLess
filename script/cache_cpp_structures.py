#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Iterable
failed_instance = []
# Ensure project root is importable when running:
#   python script/cache_cpp_structures.py
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from get_repo_structure.get_repo_structure import get_project_structure_from_scratch


DEFAULT_DATASETS = [
    "data/cpp/dealii__dealii_dataset.jsonl",
    "data/cpp/mfem__mfem_dataset.jsonl",
    # "data/cpp/OpenFOAM__OpenFOAM-dev_dataset.jsonl",
    "data/cpp/Open-Cascade-SAS__OCCT_dataset.jsonl",
]


def iter_jsonl_rows(paths: Iterable[Path]):
    for path in paths:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield path, json.loads(line)


def main():
    parser = argparse.ArgumentParser(
        description="Precompute and cache repo structures into structure/<instance_id>.json"
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=None,
        help=(
            "Path to dataset jsonl. Can be passed multiple times. "
            "Default: 4 cpp datasets in data/cpp."
        ),
    )
    parser.add_argument(
        "--target-id",
        action="append",
        default=None,
        help="Only cache specified instance_id(s). Can be passed multiple times.",
    )
    parser.add_argument(
        "--out-dir",
        default="structure",
        help="Cache output directory. Default: structure",
    )
    parser.add_argument(
        "--playground-dir",
        default="playground",
        help="Temporary playground directory. Default: playground",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if structure/<instance_id>.json already exists.",
    )
    args = parser.parse_args()

    datasets = [Path(p) for p in (args.dataset if args.dataset else DEFAULT_DATASETS)]
    for ds in datasets:
        if not ds.exists():
            raise FileNotFoundError(f"Dataset not found: {ds}")

    target_ids = set(args.target_id) if args.target_id else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    selected = 0
    generated = 0
    skipped = 0
    failed = 0

    for ds_path, row in iter_jsonl_rows(datasets):
        total += 1
        instance_id = row["instance_id"]
        if target_ids and instance_id not in target_ids:
            continue
        selected += 1

        out_file = out_dir / f"{instance_id}.json"
        if out_file.exists() and not args.force:
            skipped += 1
            continue

        repo_name = f'{row["org"]}/{row["repo"]}'
        base_sha = row["base"]["sha"]
        try:
            structure = get_project_structure_from_scratch(
                repo_name=repo_name,
                commit_id=base_sha,
                instance_id=instance_id,
                repo_playground=args.playground_dir,
            )
            with out_file.open("w", encoding="utf-8") as wf:
                json.dump(structure, wf)
            generated += 1
            print(f"[cached] {instance_id} ({repo_name}@{base_sha[:12]})")
        except Exception as e:
            failed += 1
            failed_instance.append(instance_id)
            print(
                f"[failed] {instance_id} from {ds_path}: {type(e).__name__}: {e}"
            )

    print(
        "Done. "
        f"total_rows={total}, selected={selected}, "
        f"generated={generated}, skipped_existing={skipped}, failed={failed}"
    )
    for _ in failed_instance:
        print(_)


if __name__ == "__main__":
    main()
