#!/usr/bin/env bash
set -euo pipefail
if [[ "${DEBUG:-0}" == "1" ]]; then
  set -x
fi

# Optional API env bootstrap (same style as run.sh)
if [[ -f script/api_key.sh ]]; then
  # shellcheck disable=SC1091
  set +e
  source script/api_key.sh >/dev/null 2>&1
  _api_key_status=$?
  set -e
  if [[ ${_api_key_status} -ne 0 ]]; then
    echo "[WARN] script/api_key.sh failed to source cleanly. Continue with current env."
  fi
fi

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"

print_usage() {
  cat <<'EOF'
Usage:
  bash ./script/run_line_localization_subset.sh [--subset-jsonl PATH] [--folder-name NAME] [--project-file-loc DIR] [--dry-run]

Options:
  --subset-jsonl PATH   JSONL containing selected instances (default: results/gpt5.4mini_with_anonFunc/anonymous_function.jsonl)
  --folder-name NAME    Output folder under results/ (default: gpt5.4mini_with_anonFunc_anonymous_function_line)
  --project-file-loc DIR
                        Cached structure directory (default: structure)
  --dry-run             Only print selected instance IDs; do not run localization.
  -h, --help            Show this help message.

Environment overrides:
  NJ, NUM_SETS, DATASET, SPLIT, SWEBENCH_LANG, PROJECT_FILE_LOC
EOF
}

SUBSET_JSONL="results/gpt5.4mini_with_anonFunc/anonymous_function.jsonl"
FOLDER_NAME_DEFAULT="gpt5.4mini_with_anonFunc_anonymous_function_line"
PROJECT_FILE_LOC_DEFAULT="structure"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --subset-jsonl)
      SUBSET_JSONL="$2"
      shift 2
      ;;
    --folder-name)
      FOLDER_NAME_DEFAULT="$2"
      shift 2
      ;;
    --project-file-loc)
      PROJECT_FILE_LOC_DEFAULT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1"
      print_usage
      exit 1
      ;;
  esac
done

if [[ ! -f "${SUBSET_JSONL}" ]]; then
  echo "[ERROR] subset jsonl not found: ${SUBSET_JSONL}"
  exit 1
fi

export NJ="${NJ:-8}"
export NUM_SETS="${NUM_SETS:-2}"
export DATASET="${DATASET:-local_json}"
export SPLIT="${SPLIT:-test}"
export SWEBENCH_LANG="${SWEBENCH_LANG:-cpp}"
export FOLDER_NAME="${FOLDER_NAME:-$FOLDER_NAME_DEFAULT}"
export PROJECT_FILE_LOC="${PROJECT_FILE_LOC:-$PROJECT_FILE_LOC_DEFAULT}"

if [[ "${DATASET}" != "local_json" ]]; then
  echo "[ERROR] This script currently expects DATASET=local_json."
  exit 1
fi

RUN_ROOT="results/${FOLDER_NAME}"
RUN_RELATED="${RUN_ROOT}/related_elements"
RUN_RELATED_JSONL="${RUN_RELATED}/loc_outputs.jsonl"

mkdir -p "${RUN_RELATED}"
cp "${SUBSET_JSONL}" "${RUN_RELATED_JSONL}"

TARGET_IDS=()
if command -v rg >/dev/null 2>&1; then
  while IFS= read -r one_id; do
    [[ -n "${one_id}" ]] || continue
    TARGET_IDS+=("${one_id}")
  done < <(
    rg -o '"instance_id"\s*:\s*"[^"]+"' "${RUN_RELATED_JSONL}" \
      | sed -E 's/.*"instance_id"[[:space:]]*:[[:space:]]*"([^"]+)"/\1/' \
      | awk '!seen[$0]++'
  )
else
  while IFS= read -r one_id; do
    [[ -n "${one_id}" ]] || continue
    TARGET_IDS+=("${one_id}")
  done < <(
    grep -oE '"instance_id"[[:space:]]*:[[:space:]]*"[^"]+"' "${RUN_RELATED_JSONL}" \
      | sed -E 's/.*"instance_id"[[:space:]]*:[[:space:]]*"([^"]+)"/\1/' \
      | awk '!seen[$0]++'
  )
fi

if [[ ${#TARGET_IDS[@]} -eq 0 ]]; then
  echo "[ERROR] No instance_id found in ${RUN_RELATED_JSONL}"
  exit 1
fi

echo "[INFO] subset_jsonl:    ${SUBSET_JSONL}"
echo "[INFO] run_folder:      ${RUN_ROOT}"
echo "[INFO] related_input:   ${RUN_RELATED_JSONL}"
echo "[INFO] project_struct:  ${PROJECT_FILE_LOC}"
echo "[INFO] target_count:    ${#TARGET_IDS[@]}"
echo "[INFO] target_ids:"
for iid in "${TARGET_IDS[@]}"; do
  echo "  - ${iid}"
done

MISSING_STRUCTURE=0
for iid in "${TARGET_IDS[@]}"; do
  if [[ ! -f "${PROJECT_FILE_LOC}/${iid}.json" ]]; then
    echo "[ERROR] Missing cached structure file: ${PROJECT_FILE_LOC}/${iid}.json"
    MISSING_STRUCTURE=1
  fi
done

if [[ ${MISSING_STRUCTURE} -ne 0 ]]; then
  echo "[ERROR] Cached structure is incomplete. Please prepare missing files or change --project-file-loc."
  exit 1
fi

if (( DRY_RUN == 1 )); then
  echo "[INFO] Dry run finished."
  exit 0
fi

echo "[INFO] Step 1/2: running localization3.1 per instance..."
for iid in "${TARGET_IDS[@]}"; do
  export TARGET_ID="${iid}"
  echo "[INFO] Running line-level sample localization for ${TARGET_ID}"
  ./script/localization3.1.sh
done

echo "[INFO] Step 2/2: merging samples via localization3.2..."
unset TARGET_ID || true
./script/localization3.2.sh

echo "[INFO] Done."
echo "[INFO] edit samples:     ${RUN_ROOT}/edit_location_samples/loc_outputs.jsonl"
echo "[INFO] merged outputs:   ${RUN_ROOT}/edit_location_individual/loc_merged_*_outputs.jsonl"
