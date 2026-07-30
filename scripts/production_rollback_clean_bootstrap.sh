#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
DEPLOY_ROOT="${DEPLOY_ROOT:-$HOME/temple-clean-deployments}"
DEPLOYMENT_ID="${1:-}"
SERVICE="${SERVICE:-cms}"

for command_name in docker python3 sudo; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done

case "$DEPLOYMENT_ID" in
  clean-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Usage: $0 clean-YYYYMMDDTHHMMSSZ" >&2; exit 1 ;;
esac

cd "$PROJECT_DIR"
REPORT="$DEPLOY_ROOT/reports-$DEPLOYMENT_ID/deployment.json"
test -r "$REPORT" || { echo "Unknown clean deployment: $DEPLOYMENT_ID" >&2; exit 1; }
python3 - "$REPORT" "$DEPLOYMENT_ID" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if value.get("ok") is True and value.get("deployment_id") == sys.argv[2] else 1)
PY

FAILED_ROOT="$DEPLOY_ROOT/rolled-back-$DEPLOYMENT_ID"
if [[ -e "$FAILED_ROOT" ]]; then
  echo "Rollback destination already exists: $FAILED_ROOT" >&2
  exit 1
fi
mkdir -p "$FAILED_ROOT"
chmod 700 "$FAILED_ROOT"

sudo docker compose stop "$SERVICE"
shopt -s nullglob
runtime_items=(data/*)
if [[ "${#runtime_items[@]}" -eq 0 ]]; then
  echo "No runtime data found; refusing an empty rollback" >&2
  exit 1
fi
for item in "${runtime_items[@]}"; do
  mv -- "$item" "$FAILED_ROOT/"
done
sudo docker compose rm -f "$SERVICE" >/dev/null

echo "CLEAN_BOOTSTRAP_ROLLED_BACK=true"
echo "DEPLOYMENT_ID=$DEPLOYMENT_ID"
echo "PRESERVED_FAILED_DATA=$FAILED_ROOT"
