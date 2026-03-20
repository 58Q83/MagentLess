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

export TARGET_ID=dealii-1
export FOLDER_NAME=dealii_smoke_test
export SWEBENCH_LANG=cpp
export DATASET=local_json
export SPLIT=test
export NJ=1
unset PROJECT_FILE_LOC

./script/localization1.1.sh
echo "l1.1 done"
./script/localization1.2.sh
echo "l1.2 done"
./script/localization1.3.sh
echo "l1.3 done"
./script/localization1.4.sh
echo "l1.4 done"
./script/localization2.1.sh
echo "l2.1 done"
