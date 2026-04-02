#!/usr/bin/env bash
set -euo pipefail
if [[ "${DEBUG:-0}" == "1" ]]; then
  set -x
fi

# Optional API env bootstrap (same style as run.sh)
if [[ -f script/api_key.sh ]]; then
  # shellcheck disable=SC1091
  source script/api_key.sh
else
  echo "[WARN] script/api_key.sh not found. Please export API env vars manually."
fi

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"

print_usage() {
  cat <<'EOF'
Usage: bash ./script/file_loacalization.sh [--embedding|--skip-embedding] [-h|--help]

Options:
  --embedding        Run embedding stage (localization1.2.sh and localization1.3.sh)
  --skip-embedding   Skip embedding stage (default)
  -h, --help         Show this help message
EOF
}

# Embedding stage is skipped by default to preserve existing behavior.
RUN_EMBEDDING=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --embedding)
      RUN_EMBEDDING=1
      shift
      ;;
    --skip-embedding)
      RUN_EMBEDDING=0
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

# You can override all of these before running:
#   FOLDER_NAME SWEBENCH_LANG PROJECT_FILE_LOC DATASET SPLIT NJ NUM_SETS
export NJ="${NJ:-2}"
export NUM_SETS="${NUM_SETS:-2}"
USER_DEFINED_FOLDER_NAME=0
if [[ -n "${FOLDER_NAME:-}" ]]; then
  USER_DEFINED_FOLDER_NAME=1
fi
export FOLDER_NAME="${FOLDER_NAME:-}"
export SWEBENCH_LANG="${SWEBENCH_LANG:-cpp}"
export PROJECT_FILE_LOC="${PROJECT_FILE_LOC:-structure}"
export DATASET="${DATASET:-local_json}"
export SPLIT="${SPLIT:-test}"
export FILTER_TOP_N=500
if [[ "${DATASET}" != "local_json" ]]; then
  echo "[ERROR] This manual-by-project script currently supports DATASET=local_json only."
  exit 1
fi

if [[ "${SWEBENCH_LANG}" == "javascript" ]]; then
  DATA_LANG_DIR="js"
elif [[ "${SWEBENCH_LANG}" == "typescript" ]]; then
  DATA_LANG_DIR="ts"
else
  DATA_LANG_DIR="${SWEBENCH_LANG}"
fi

DATA_DIR="data/${DATA_LANG_DIR}"

if ! find "${DATA_DIR}" -maxdepth 1 -name "*.jsonl" | grep -q .; then
  echo "[ERROR] No jsonl files found in ${DATA_DIR}. Please prepare dataset files first."
  exit 1
fi

sanitize_name() {
  printf "%s" "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//'
}

build_folder_name() {
  local project_file="$1"
  local project_name
  local model_name
  project_name="$(basename "${project_file}" .jsonl)"
  model_name="${OPENAI_MODEL:-unknown-model}"
  project_name="$(sanitize_name "${project_name}")"
  model_name="$(sanitize_name "${model_name}")"

  if [[ -z "${project_name}" ]]; then
    project_name="project"
  fi
  if [[ -z "${model_name}" ]]; then
    model_name="model"
  fi

  printf "%s.%s" "${project_name}" "${model_name}"
}

run_selected_project_batch() {
  local selected_file="$1"
  local selected_name
  selected_name="$(basename "${selected_file}")"
  local backup_dir
  backup_dir="$(mktemp -d "${TMPDIR:-/tmp}/magentless_${DATA_LANG_DIR}_XXXXXX")"

  (
    cleanup() {
      set +e
      while IFS= read -r one_file; do
        mv "${one_file}" "${DATA_DIR}/"
      done < <(find "${backup_dir}" -maxdepth 1 -name "*.jsonl" | sort)
      rmdir "${backup_dir}" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT

    # Keep only the selected project's dataset file in data/<lang> during this run.
    while IFS= read -r one_file; do
      if [[ "${one_file}" != "${selected_file}" ]]; then
        mv "${one_file}" "${backup_dir}/"
      fi
    done < <(find "${DATA_DIR}" -maxdepth 1 -name "*.jsonl" | sort)

    unset TARGET_ID
    echo "[INFO] Running full localization pipeline for project ${selected_name}"
    ./script/localization1.1.sh
    if (( RUN_EMBEDDING == 1 )); then
      echo "[INFO] Embedding stage enabled. Running localization1.2 and localization1.3."
      ./script/localization1.2.sh
      ./script/localization1.3.sh
    else
      echo "[INFO] Embedding stage skipped."
    fi
  )
}

list_projects() {
  PROJECT_FILES=()
  while IFS= read -r one_file; do
    PROJECT_FILES+=("${one_file}")
  done < <(find "${DATA_DIR}" -maxdepth 1 -name "*.jsonl" | sort)

  if [[ ${#PROJECT_FILES[@]} -eq 0 ]]; then
    echo "[ERROR] No project files detected in ${DATA_DIR}."
    exit 1
  fi

  echo ""
  echo "Available projects in ${DATA_DIR}:"
  local i
  for i in "${!PROJECT_FILES[@]}"; do
    printf "%3d) %s\n" "$((i + 1))" "$(basename "${PROJECT_FILES[$i]}")"
  done
  echo ""
}

count_instance_ids() {
  local project_file="$1"
  local count=0
  if command -v rg >/dev/null 2>&1; then
    count="$(
      rg -o '"instance_id"\s*:\s*"[^"]+"' "${project_file}" \
        | sed -E 's/.*"instance_id"\s*:\s*"([^"]+)"/\1/' \
        | awk '!seen[$0]++' \
        | wc -l \
        | tr -d ' '
    )"
  else
    count="$(
      grep -oE '"instance_id"[[:space:]]*:[[:space:]]*"[^"]+"' "${project_file}" \
        | sed -E 's/.*"instance_id"[[:space:]]*:[[:space:]]*"([^"]+)"/\1/' \
        | awk '!seen[$0]++' \
        | wc -l \
        | tr -d ' '
    )"
  fi
  if [[ "${count}" == "0" ]]; then
    echo "[ERROR] No instance_id found in ${project_file}."
    return 1
  fi
  PROJECT_INSTANCE_COUNT="${count}"
}

while true; do
  list_projects

  read -r -p "Select one project to run (number, or q to quit): " PICK
  if [[ "${PICK}" == "q" || "${PICK}" == "Q" ]]; then
    echo "Exit without running more projects."
    break
  fi

  if ! [[ "${PICK}" =~ ^[0-9]+$ ]]; then
    echo "[WARN] Invalid input: ${PICK}. Please input a number."
    continue
  fi

  IDX=$((PICK - 1))
  if (( IDX < 0 || IDX >= ${#PROJECT_FILES[@]} )); then
    echo "[WARN] Out of range: ${PICK}."
    continue
  fi

  SELECTED_FILE="${PROJECT_FILES[$IDX]}"
  SELECTED_NAME="$(basename "${SELECTED_FILE}")"

  if (( USER_DEFINED_FOLDER_NAME == 0 )); then
    export FOLDER_NAME
    FOLDER_NAME="$(build_folder_name "${SELECTED_FILE}")"
  fi

  count_instance_ids "${SELECTED_FILE}"

  echo "[INFO] Selected project file: ${SELECTED_NAME}"
  echo "[INFO] Total instances to run: ${PROJECT_INSTANCE_COUNT}"

  read -r -p "Run this project now? [y/N]: " CONFIRM
  if [[ "${CONFIRM}" != "y" && "${CONFIRM}" != "Y" ]]; then
    echo "[INFO] Skip ${SELECTED_NAME}."
    continue
  fi

  run_selected_project_batch "${SELECTED_FILE}"

  echo "[INFO] Project ${SELECTED_NAME} finished."
  echo "[INFO] Results are under results/${FOLDER_NAME}/"

  read -r -p "Continue and select another project? [y/N]: " CONTINUE_PICK
  if [[ "${CONTINUE_PICK}" != "y" && "${CONTINUE_PICK}" != "Y" ]]; then
    break
  fi
done
