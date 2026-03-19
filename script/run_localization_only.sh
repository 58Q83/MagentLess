#!/usr/bin/env bash
set -euo pipefail
set -x

# Optional API env bootstrap (same style as run.sh)
if [[ -f script/api_key.sh ]]; then
  # shellcheck disable=SC1091
  source script/api_key.sh
else
  echo "[WARN] script/api_key.sh not found. Please export API env vars manually."
fi

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"

# You can override all of these before running:
#   FOLDER_NAME SWEBENCH_LANG PROJECT_FILE_LOC DATASET SPLIT NJ NUM_SETS TARGET_ID
export TARGET_ID="${TARGET_ID:-}"
export NJ="${NJ:-50}"
export NUM_SETS="${NUM_SETS:-2}"
export FOLDER_NAME="${FOLDER_NAME:-def4cae_cpp_localization}"
export SWEBENCH_LANG="${SWEBENCH_LANG:-cpp}"
export PROJECT_FILE_LOC="${PROJECT_FILE_LOC:-structure}"
export DATASET="${DATASET:-local_json}"
export SPLIT="${SPLIT:-test}"

# Basic sanity checks for local custom dataset mode.
if [[ "${DATASET}" == "local_json" && "${SWEBENCH_LANG}" == "cpp" ]]; then
  if ! find data/cpp -maxdepth 1 -name "*.jsonl" | grep -q .; then
    echo "[ERROR] No jsonl files found in data/cpp. Please prepare dataset files first."
    exit 1
  fi
fi

# Localization-only pipeline
./script/localization1.1.sh
./script/localization1.2.sh
./script/localization1.3.sh
./script/localization1.4.sh
./script/localization2.1.sh

echo "Localization finished. Results are under results/${FOLDER_NAME}/"
