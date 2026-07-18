#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/temple-backups}"
BACKUP_ID="${1:-${BACKUP_ID:-}}"
LOCAL_BACKUP_SHA256="${LOCAL_BACKUP_SHA256:-}"
SERVICE="cms"
TEST_PORT="18003"
PAVEL_ID="56871da9-3f57-4ff0-b405-3127668f7cad"
PUBLIC_BASE_URL_EXPECTED="https://temple.gbvolkoff.name:8443"
EXPECTED_SCHEMA_BEFORE="3"
EXPECTED_SCHEMA_AFTER="4"
EXPECTED_CONTENTS="1757"
EXPECTED_PUBLISHED="1"
EXPECTED_REVIEW_REQUIRED="1756"
EXPECTED_REDIRECTS="1606"

case "$BACKUP_ID" in
  baseline-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Usage: LOCAL_BACKUP_SHA256=<sha256> $0 baseline-YYYYMMDDTHHMMSSZ" >&2; exit 1 ;;
esac

for command_name in git curl sqlite3 tar sha256sum python3 sudo docker grep seq date awk tr; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done

cd "$PROJECT_DIR"
ARCHIVE="$BACKUP_ROOT/$BACKUP_ID.tar.gz"
CHECKSUM="$ARCHIVE.sha256"
BASELINE_REPORT="$BACKUP_ROOT/$BACKUP_ID-baseline-report.json"
RESTORE_REPORT="$BACKUP_ROOT/$BACKUP_ID-restore-verification.json"
TEST_ROOT="$BACKUP_ROOT/stage3-test-$BACKUP_ID"
TEST_DATA="$TEST_ROOT/$BACKUP_ID"
TEST_CONTAINER="temple-stage3-test-${BACKUP_ID//[^a-zA-Z0-9]/-}"
POST_REPORT="$BACKUP_ROOT/$BACKUP_ID-stage3-post-report.json"

for file in "$ARCHIVE" "$CHECKSUM" "$BASELINE_REPORT" "$RESTORE_REPORT"; do
  test -r "$file" || { echo "Required backup artifact is missing: $file" >&2; exit 1; }
done
(
  cd "$BACKUP_ROOT"
  sha256sum -c "$(basename "$CHECKSUM")"
)
SERVER_SHA256="$(awk '{print tolower($1)}' "$CHECKSUM")"
if [[ -z "$LOCAL_BACKUP_SHA256" || "${LOCAL_BACKUP_SHA256,,}" != "$SERVER_SHA256" ]]; then
  echo "LOCAL_BACKUP_SHA256 must match the verified Windows copy before deployment" >&2
  exit 1
fi
python3 -c 'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(0 if data.get("ok") is True else 1)' "$RESTORE_REPORT"

report_value() {
  python3 -c 'import functools,json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); print(functools.reduce(dict.__getitem__, sys.argv[2].split("."), data))' "$BASELINE_REPORT" "$1"
}

require_equal() {
  label="$1"
  actual="$2"
  expected="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "$label mismatch: expected $expected, received $actual" >&2
    exit 1
  fi
}

database_value() {
  sqlite3 -readonly "$1" "$2"
}

validate_public_base_url() {
  python3 - "$1" "$PUBLIC_BASE_URL_EXPECTED" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected = sys.argv[2]
values = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    values[name.strip()] = value.strip().strip('"').strip("'")
raise SystemExit(0 if values.get("PUBLIC_BASE_URL", "").rstrip("/") == expected else 1)
PY
}

CONTENTS="$(report_value database.metrics.contents)"
REVISIONS="$(report_value database.metrics.revisions)"
PUBLISHED="$(report_value database.by_status.published)"
REVIEW_REQUIRED="$(report_value database.review_required)"
CURRENT_SHA="$(git rev-parse HEAD)"
BACKUP_SHA="$(report_value source.git_sha)"
require_equal "deployment branch" "$(git branch --show-current)" "main"
require_equal "origin/main SHA" "$(git rev-parse origin/main)" "$CURRENT_SHA"
git merge-base --is-ancestor "$BACKUP_SHA" "$CURRENT_SHA" || {
  echo "The backup checkout $BACKUP_SHA is not an ancestor of deployment $CURRENT_SHA" >&2
  exit 1
}
require_equal "backup schema version" "$(report_value database.schema_version)" "$EXPECTED_SCHEMA_BEFORE"
require_equal "backup content count" "$CONTENTS" "$EXPECTED_CONTENTS"
require_equal "backup published count" "$PUBLISHED" "$EXPECTED_PUBLISHED"
require_equal "backup review-required count" "$REVIEW_REQUIRED" "$EXPECTED_REVIEW_REQUIRED"

BACKUP_EPOCH="$(date -u -d "$(report_value generated_at)" +%s)"
NOW_EPOCH="$(date -u +%s)"
if (( NOW_EPOCH - BACKUP_EPOCH > 86400 )); then
  echo "The production backup is older than 24 hours; create and copy a fresh baseline" >&2
  exit 1
fi

if ! git diff --quiet -- . ':(exclude)data/**'; then
  echo "Tracked code/configuration changes are present; refusing production deployment" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "The deployment checkout is not clean; refusing production deployment" >&2
  exit 1
fi
validate_public_base_url .env || {
  echo "PUBLIC_BASE_URL must be $PUBLIC_BASE_URL_EXPECTED in .env" >&2
  exit 1
}
test "$(sqlite3 -readonly data/cms.sqlite3 'PRAGMA quick_check;')" = "ok"
test -z "$(sqlite3 -readonly data/cms.sqlite3 'PRAGMA foreign_key_check;')"
require_equal "production schema before stage 3" "$(database_value data/cms.sqlite3 'SELECT COALESCE(MAX(version),0) FROM schema_migrations;')" "$EXPECTED_SCHEMA_BEFORE"
require_equal "production content count" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM contents;')" "$CONTENTS"
require_equal "production revision count" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM revisions;')" "$REVISIONS"
require_equal "production published count" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM contents WHERE published_version IS NOT NULL AND status NOT IN ('archived','trash');")" "$PUBLISHED"
require_equal "production review-required count" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM contents WHERE migration_review_required=1;')" "$REVIEW_REQUIRED"
require_equal "production redirect count" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM redirects;')" "$EXPECTED_REDIRECTS"
require_equal "Pavel publication before stage 3" "$(database_value data/cms.sqlite3 "SELECT status||'|'||version||'|'||published_version||'|'||(published_slug=slug) FROM contents WHERE id='$PAVEL_ID';")" "published|8|8|1"
PAVEL_SLUG="$(database_value data/cms.sqlite3 "SELECT published_slug FROM contents WHERE id='$PAVEL_ID';")"
PAVEL_TITLE="$(database_value data/cms.sqlite3 "SELECT title FROM contents WHERE id='$PAVEL_ID';")"
PAVEL_LEGACY="$(database_value data/cms.sqlite3 "SELECT legacy_url FROM contents WHERE id='$PAVEL_ID';")"
PAVEL_CLEAN="/about/clergy/$PAVEL_SLUG"

if [[ -e "$TEST_ROOT" ]]; then
  echo "Test restore destination already exists: $TEST_ROOT" >&2
  exit 1
fi
if command -v ss >/dev/null 2>&1 && ss -ltn | grep -q ":$TEST_PORT "; then
  echo "Stage 3 test port $TEST_PORT is already in use" >&2
  exit 1
fi

sudo docker compose build "$SERVICE"
mapfile -t IMAGE_REFERENCES < <(sudo docker compose config --images)
require_equal "Compose image count" "${#IMAGE_REFERENCES[@]}" "1"
NEW_IMAGE_ID="$(sudo docker image inspect --format '{{.Id}}' "${IMAGE_REFERENCES[0]}")"
test -n "$NEW_IMAGE_ID"

mkdir -p "$TEST_ROOT"
chmod 700 "$TEST_ROOT"
tar -C "$TEST_ROOT" -xzf "$ARCHIVE"
validate_public_base_url "$TEST_DATA/.env" || {
  echo "The restored .env does not contain the expected PUBLIC_BASE_URL" >&2
  exit 1
}

TEST_STARTED=0
cleanup() {
  exit_code=$?
  if [[ "$TEST_STARTED" -eq 1 ]]; then
    sudo docker stop "$TEST_CONTAINER" >/dev/null 2>&1 || true
  fi
  case "$TEST_ROOT" in
    "$BACKUP_ROOT"/stage3-test-baseline-*) rm -rf -- "$TEST_ROOT" ;;
    *) echo "Refusing to remove unsafe test path: $TEST_ROOT" >&2 ;;
  esac
  exit "$exit_code"
}
trap cleanup EXIT

sudo docker run -d --rm \
  --name "$TEST_CONTAINER" \
  --env-file "$TEST_DATA/.env" \
  -p "127.0.0.1:$TEST_PORT:8000" \
  -v "$TEST_DATA/data:/data" \
  "$NEW_IMAGE_ID" >/dev/null
TEST_STARTED=1
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$TEST_PORT/api/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$TEST_PORT/api/health" >/dev/null
python3 -m server.migrations verify --database "$TEST_DATA/data/cms.sqlite3" >/dev/null
test "$(sqlite3 -readonly "$TEST_DATA/data/cms.sqlite3" 'PRAGMA quick_check;')" = "ok"
test -z "$(sqlite3 -readonly "$TEST_DATA/data/cms.sqlite3" 'PRAGMA foreign_key_check;')"
require_equal "test schema version" "$(database_value "$TEST_DATA/data/cms.sqlite3" 'SELECT MAX(version) FROM schema_migrations;')" "$EXPECTED_SCHEMA_AFTER"
require_equal "test content count" "$(database_value "$TEST_DATA/data/cms.sqlite3" 'SELECT COUNT(*) FROM contents;')" "$CONTENTS"
require_equal "test revision count" "$(database_value "$TEST_DATA/data/cms.sqlite3" 'SELECT COUNT(*) FROM revisions;')" "$REVISIONS"
require_equal "test published count" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM contents WHERE published_version IS NOT NULL AND status NOT IN ('archived','trash');")" "$PUBLISHED"
require_equal "test review-required count" "$(database_value "$TEST_DATA/data/cms.sqlite3" 'SELECT COUNT(*) FROM contents WHERE migration_review_required=1;')" "$REVIEW_REQUIRED"
require_equal "test redirect count" "$(database_value "$TEST_DATA/data/cms.sqlite3" 'SELECT COUNT(*) FROM redirects;')" "$EXPECTED_REDIRECTS"
require_equal "test hash redirect count" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM redirects WHERE new_path LIKE '/#/%';")" "0"
require_equal "test non-301 redirect count" "$(database_value "$TEST_DATA/data/cms.sqlite3" 'SELECT COUNT(*) FROM redirects WHERE status_code != 301;')" "0"
require_equal "test Pavel publication pointer" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT status||'|'||version||'|'||published_version||'|'||(published_slug=slug) FROM contents WHERE id='$PAVEL_ID';")" "published|8|8|1"

for route in / /schedule /about /parish /school /news /gallery /leaflet /media; do
  require_equal "test route $route" "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TEST_PORT$route")" "200"
done
require_equal "test Pavel clean route" "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TEST_PORT$PAVEL_CLEAN")" "200"
curl -fsS "http://127.0.0.1:$TEST_PORT$PAVEL_CLEAN" | grep -Fq "$PAVEL_TITLE"
require_equal "test wrong-type route" "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TEST_PORT/news/$PAVEL_SLUG")" "404"
require_equal "test legacy redirect status" "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TEST_PORT$PAVEL_LEGACY")" "301"
LEGACY_LOCATION="$(curl -sS -o /dev/null -D - "http://127.0.0.1:$TEST_PORT$PAVEL_LEGACY" | awk 'tolower($1)=="location:" {print $2}' | tr -d '\r')"
require_equal "test legacy redirect target" "$LEGACY_LOCATION" "$PAVEL_CLEAN"
require_equal "test trailing-slash status" "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TEST_PORT/news/?source=rollout")" "308"
TRAILING_LOCATION="$(curl -sS -o /dev/null -D - "http://127.0.0.1:$TEST_PORT/news/?source=rollout" | awk 'tolower($1)=="location:" {print $2}' | tr -d '\r')"
require_equal "test trailing-slash target" "$TRAILING_LOCATION" "/news?source=rollout"
curl -fsS "http://127.0.0.1:$TEST_PORT/cms.html" | grep -Fq "$PUBLIC_BASE_URL_EXPECTED/"

sudo docker stop "$TEST_CONTAINER" >/dev/null
TEST_STARTED=0
case "$TEST_ROOT" in
  "$BACKUP_ROOT"/stage3-test-baseline-*) rm -rf -- "$TEST_ROOT" ;;
  *) echo "Refusing to remove unsafe test path: $TEST_ROOT" >&2; exit 1 ;;
esac
trap - EXIT

# The working database is touched only after the restored copy passes every check.
require_equal "production schema immediately before deployment" "$(database_value data/cms.sqlite3 'SELECT MAX(version) FROM schema_migrations;')" "$EXPECTED_SCHEMA_BEFORE"
DEPLOYMENT_STARTED="$(date +%s)"
sudo docker compose up -d --no-deps "$SERVICE"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null
python3 -m server.migrations verify --database data/cms.sqlite3 >/dev/null
test "$(sqlite3 -readonly data/cms.sqlite3 'PRAGMA quick_check;')" = "ok"
test -z "$(sqlite3 -readonly data/cms.sqlite3 'PRAGMA foreign_key_check;')"
require_equal "production schema after stage 3" "$(database_value data/cms.sqlite3 'SELECT MAX(version) FROM schema_migrations;')" "$EXPECTED_SCHEMA_AFTER"
require_equal "production content count after stage 3" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM contents;')" "$CONTENTS"
require_equal "production revision count after stage 3" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM revisions;')" "$REVISIONS"
require_equal "production published count after stage 3" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM contents WHERE published_version IS NOT NULL AND status NOT IN ('archived','trash');")" "$PUBLISHED"
require_equal "production review-required count after stage 3" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM contents WHERE migration_review_required=1;')" "$REVIEW_REQUIRED"
require_equal "production redirect count after stage 3" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM redirects;')" "$EXPECTED_REDIRECTS"
require_equal "production hash redirect count" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM redirects WHERE new_path LIKE '/#/%';")" "0"
require_equal "production Pavel publication pointer" "$(database_value data/cms.sqlite3 "SELECT status||'|'||version||'|'||published_version||'|'||(published_slug=slug) FROM contents WHERE id='$PAVEL_ID';")" "published|8|8|1"

require_equal "external site status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/")" "200"
require_equal "external Pavel status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED$PAVEL_CLEAN")" "200"
require_equal "public-domain CMS status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/cms.html")" "404"
require_equal "external CMS status" "$(curl -ksS -o /dev/null -w '%{http_code}' 'https://cms.temple.gbvolkoff.name:8443/cms.html')" "200"
require_equal "external health status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/api/health")" "200"
require_equal "external legacy redirect status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED$PAVEL_LEGACY")" "301"
EXTERNAL_LEGACY_LOCATION="$(curl -ksS -o /dev/null -D - "$PUBLIC_BASE_URL_EXPECTED$PAVEL_LEGACY" | awk 'tolower($1)=="location:" {print $2}' | tr -d '\r')"
require_equal "external legacy redirect target" "$EXTERNAL_LEGACY_LOCATION" "$PAVEL_CLEAN"

python3 -m server.baseline report \
  --database data/cms.sqlite3 \
  --media-dir data/media \
  --git-sha "$CURRENT_SHA" \
  --tag "stage3-clean-public-urls" \
  --baseline-tag-sha "$CURRENT_SHA" \
  --image-id "$NEW_IMAGE_ID" \
  --env-file .env \
  --output "$POST_REPORT"
chmod 600 "$POST_REPORT"
DEPLOYMENT_SECONDS=$(( $(date +%s) - DEPLOYMENT_STARTED ))

echo "STAGE3_APPLIED=true"
echo "BACKUP_ID=$BACKUP_ID"
echo "IMPLEMENTATION_SHA=$CURRENT_SHA"
echo "IMAGE_ID=$NEW_IMAGE_ID"
echo "SERVER_ARCHIVE_SHA256=$SERVER_SHA256"
echo "PAVEL_CLEAN_URL=$PUBLIC_BASE_URL_EXPECTED$PAVEL_CLEAN"
echo "POST_REPORT=$POST_REPORT"
echo "DEPLOYMENT_SECONDS=$DEPLOYMENT_SECONDS"
