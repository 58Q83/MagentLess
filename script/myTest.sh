#!/usr/bin/env bash
set -euo pipefail

# Always run from repo root so relative paths work.
cd "$(dirname "$0")/.."

# Load API settings if present.
if [[ -f script/api_key.sh ]]; then
  # shellcheck disable=SC1091
  source script/api_key.sh
fi

export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"

export TARGET_ID=OCCT-1
export FOLDER_NAME=occt_smoke_test
export SWEBENCH_LANG=cpp
export DATASET=local_json
export SPLIT=test
export NJ=1
unset PROJECT_FILE_LOC

./script/localization1.1.sh
./script/localization1.2.sh
./script/localization1.3.sh
./script/localization1.4.sh
./script/localization2.1.sh
