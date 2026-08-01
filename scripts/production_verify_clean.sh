#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
STATE_ROOT="${STATE_ROOT:-$PROJECT_DIR/.clean-deploy}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
SERVICE="${SERVICE:-cms}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"
EXPECTED_CONTENT_SCHEMA="${EXPECTED_CONTENT_SCHEMA:-1.6.0}"
VERIFY_EXTERNAL="${VERIFY_EXTERNAL:-0}"
DEPLOY_ID="${1:-}"
ATTEMPT_ID="${2:-}"

case "$DEPLOY_ID" in
  public-source-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Usage: $0 public-source-YYYYMMDDTHHMMSSZ" >&2; exit 2 ;;
esac

for command_name in curl python3 sudo docker sqlite3 tr; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done

cd "$PROJECT_DIR"
BUNDLE_STATE="$STATE_ROOT/$DEPLOY_ID"
if [[ -z "$ATTEMPT_ID" ]]; then
  test -r "$BUNDLE_STATE/latest-success" || { echo "Successful deployment pointer is missing" >&2; exit 1; }
  ATTEMPT_ID="$(tr -d '\r\n' <"$BUNDLE_STATE/latest-success")"
fi
case "$ATTEMPT_ID" in
  attempt-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Unsafe deployment attempt: $ATTEMPT_ID" >&2; exit 1 ;;
esac
SOURCE_DIR="$BUNDLE_STATE/$ATTEMPT_ID/source"
REPORTS_DIR="$BUNDLE_STATE/$ATTEMPT_ID/reports"
test -d "$SOURCE_DIR" || { echo "Source evidence is missing: $SOURCE_DIR" >&2; exit 1; }
test -r data/cms.sqlite3 || { echo "Production database is missing" >&2; exit 1; }
mkdir -p "$REPORTS_DIR"

python3 scripts/validate_production_env.py "$ENV_FILE" >"$REPORTS_DIR/environment-reverify.json"
python3 scripts/validate_source_bundle.py verify "$SOURCE_DIR" >"$REPORTS_DIR/source-bundle-reverify.json"
python3 scripts/validate_source_bundle.py verify-database "$SOURCE_DIR" \
  --database data/cms.sqlite3 --post-activation >"$REPORTS_DIR/database-reverify.json"
test "$(sqlite3 -readonly data/cms.sqlite3 'PRAGMA quick_check;')" = "ok"
test -z "$(sqlite3 -readonly data/cms.sqlite3 'PRAGMA foreign_key_check;')"

curl -fsS "$HEALTH_URL" >"$REPORTS_DIR/health-reverify.json"
python3 -c 'import json,sys; data=json.load(open(sys.argv[1],encoding="utf-8")); assert data["status"]=="ok" and data["schema_version"]==sys.argv[2]' "$REPORTS_DIR/health-reverify.json" "$EXPECTED_CONTENT_SCHEMA"
curl -fsS "http://127.0.0.1:8000/cms.html" >/dev/null
curl -fsS "http://127.0.0.1:8000/api/public/content?limit=1" >/dev/null
sudo docker compose exec -T "$SERVICE" python -m server.migrations verify --database /data/cms.sqlite3 >"$REPORTS_DIR/migrations-reverify.json"
sudo docker compose exec -T "$SERVICE" python -m server.search verify --database /data/cms.sqlite3 >"$REPORTS_DIR/search-reverify.txt"
sudo docker compose exec -T "$SERVICE" python -m server.migration_preparation verify --database /data/cms.sqlite3 >"$REPORTS_DIR/preparation-reverify.json"

if [[ "$VERIFY_EXTERNAL" == "1" ]]; then
  PUBLIC_BASE_URL="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["public_base_url"])' < <(python3 scripts/validate_production_env.py "$ENV_FILE"))"
  curl -fsS "$PUBLIC_BASE_URL/" >/dev/null
  curl -fsS "$PUBLIC_BASE_URL/api/health" >/dev/null
fi

chmod 600 "$REPORTS_DIR"/*
echo "CLEAN_DEPLOYMENT_VERIFIED=true"
echo "DEPLOYMENT_ID=$DEPLOY_ID"
echo "DEPLOYMENT_ATTEMPT=$ATTEMPT_ID"
echo "REPORTS_DIR=$REPORTS_DIR"
