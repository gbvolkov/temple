#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/temple-backups}"
BACKUP_ID="${1:-${BACKUP_ID:-}}"
LOCAL_BACKUP_SHA256="${LOCAL_BACKUP_SHA256:-}"
SERVICE="cms"
TEST_PORT="18004"
PAVEL_ID="56871da9-3f57-4ff0-b405-3127668f7cad"
PUBLIC_BASE_URL_EXPECTED="https://temple.gbvolkoff.name:8443"
EXPECTED_SCHEMA="4"
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
TEST_ROOT="$BACKUP_ROOT/stage4-test-$BACKUP_ID"
TEST_DATA="$TEST_ROOT/$BACKUP_ID"
TEST_CONTAINER="temple-stage4-test-${BACKUP_ID//[^a-zA-Z0-9]/-}"
POST_REPORT="$BACKUP_ROOT/$BACKUP_ID-stage4-post-report.json"

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
  local label="$1" actual="$2" expected="$3"
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

validate_database() {
  local database="$1" label="$2"
  test "$(database_value "$database" 'PRAGMA quick_check;')" = "ok"
  test -z "$(database_value "$database" 'PRAGMA foreign_key_check;')"
  require_equal "$label schema" "$(database_value "$database" 'SELECT COALESCE(MAX(version),0) FROM schema_migrations;')" "$EXPECTED_SCHEMA"
  require_equal "$label contents" "$(database_value "$database" 'SELECT COUNT(*) FROM contents;')" "$CONTENTS"
  require_equal "$label revisions" "$(database_value "$database" 'SELECT COUNT(*) FROM revisions;')" "$REVISIONS"
  require_equal "$label public materials" "$(database_value "$database" "SELECT COUNT(*) FROM contents WHERE published_version IS NOT NULL AND status NOT IN ('archived','trash');")" "$PUBLISHED"
  require_equal "$label review-required" "$(database_value "$database" 'SELECT COUNT(*) FROM contents WHERE migration_review_required=1;')" "$REVIEW_REQUIRED"
  require_equal "$label redirects" "$(database_value "$database" 'SELECT COUNT(*) FROM redirects;')" "$EXPECTED_REDIRECTS"
  require_equal "$label hash redirects" "$(database_value "$database" "SELECT COUNT(*) FROM redirects WHERE new_path LIKE '/#/%';")" "0"
  require_equal "$label Pavel pointer" "$(database_value "$database" "SELECT status||'|'||version||'|'||published_version||'|'||(published_slug=slug) FROM contents WHERE id='$PAVEL_ID';")" "published|8|8|1"
  require_equal "$label published contacts" "$(database_value "$database" "SELECT COUNT(*) FROM contents WHERE content_type='site_contact' AND published_version IS NOT NULL AND status NOT IN ('archived','trash');")" "1"
  require_equal "$label unreviewed public contacts" "$(database_value "$database" "SELECT COUNT(*) FROM contents WHERE content_type='site_contact' AND published_version IS NOT NULL AND migration_review_required!=0;")" "0"
  require_equal "$label duplicate singleton placements" "$(database_value "$database" "SELECT COUNT(*) FROM (SELECT json_extract(r.snapshot_json,'$.data.placement') placement,COUNT(*) amount FROM contents c JOIN revisions r ON r.content_id=c.id AND r.version=c.published_version WHERE c.content_type='page' AND c.published_version IS NOT NULL AND c.status NOT IN ('archived','trash') AND json_extract(r.snapshot_json,'$.data.placement') IN ('about_history','school_home','schedule_info') GROUP BY placement HAVING amount>1);")" "0"
  require_equal "$label invalid related sections" "$(database_value "$database" "SELECT COUNT(*) FROM contents c WHERE c.content_type IN ('news','gallery') AND COALESCE(json_extract(c.data_json,'$.related_section'),'')!='' AND NOT EXISTS (SELECT 1 FROM contents s WHERE s.content_type='parish_section' AND (s.id=json_extract(c.data_json,'$.related_section') OR s.slug=json_extract(c.data_json,'$.related_section')));")" "0"
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
require_equal "backup schema version" "$(report_value database.schema_version)" "$EXPECTED_SCHEMA"
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
validate_database data/cms.sqlite3 "production before stage 4"

CONTACT_ADDRESS="$(database_value data/cms.sqlite3 "SELECT json_extract(r.snapshot_json,'$.data.address') FROM contents c JOIN revisions r ON r.content_id=c.id AND r.version=c.published_version WHERE c.content_type='site_contact' AND c.status NOT IN ('archived','trash') LIMIT 1;")"
CONTACT_PHONE="$(database_value data/cms.sqlite3 "SELECT json_extract(r.snapshot_json,'$.data.phone') FROM contents c JOIN revisions r ON r.content_id=c.id AND r.version=c.published_version WHERE c.content_type='site_contact' AND c.status NOT IN ('archived','trash') LIMIT 1;")"
test -n "$CONTACT_ADDRESS" || { echo "Published site_contact.address is required before stage 4" >&2; exit 1; }
test -n "$CONTACT_PHONE" || { echo "Published site_contact.phone is required before stage 4" >&2; exit 1; }

if [[ -e "$TEST_ROOT" ]]; then
  echo "Test restore destination already exists: $TEST_ROOT" >&2
  exit 1
fi
if command -v ss >/dev/null 2>&1 && ss -ltn | grep -q ":$TEST_PORT "; then
  echo "Stage 4 test port $TEST_PORT is already in use" >&2
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
  local exit_code=$?
  if [[ "$TEST_STARTED" -eq 1 ]]; then
    sudo docker stop "$TEST_CONTAINER" >/dev/null 2>&1 || true
  fi
  case "$TEST_ROOT" in
    "$BACKUP_ROOT"/stage4-test-baseline-*) rm -rf -- "$TEST_ROOT" ;;
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
curl -fsS "http://127.0.0.1:$TEST_PORT/api/health" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["status"]=="ok" and data["schema_version"]=="1.1.0"'
python3 -m server.migrations verify --database "$TEST_DATA/data/cms.sqlite3" >/dev/null
validate_database "$TEST_DATA/data/cms.sqlite3" "restored stage 4 image"

for route in / /schedule /about /parish /school /news /gallery /leaflet /media; do
  require_equal "test route $route" "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TEST_PORT$route")" "200"
done
PAVEL_SLUG="$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT published_slug FROM contents WHERE id='$PAVEL_ID';")"
PAVEL_CLEAN="/about/clergy/$PAVEL_SLUG"
require_equal "test Pavel clean route" "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$TEST_PORT$PAVEL_CLEAN")" "200"
curl -fsS "http://127.0.0.1:$TEST_PORT/about" | grep -Fq "$CONTACT_ADDRESS"
curl -fsS "http://127.0.0.1:$TEST_PORT/about" | grep -Fq "$CONTACT_PHONE"
if curl -fsS "http://127.0.0.1:$TEST_PORT/cms.html" | grep -Fq 'temple-demo'; then
  echo "CMS still exposes the demonstration password" >&2
  exit 1
fi

sudo docker stop "$TEST_CONTAINER" >/dev/null
TEST_STARTED=0
case "$TEST_ROOT" in
  "$BACKUP_ROOT"/stage4-test-baseline-*) rm -rf -- "$TEST_ROOT" ;;
  *) echo "Refusing to remove unsafe test path: $TEST_ROOT" >&2; exit 1 ;;
esac
trap - EXIT

# Production is changed only after the restored copy passes every check.
validate_database data/cms.sqlite3 "production immediately before deployment"
DEPLOYMENT_STARTED="$(date +%s)"
sudo docker compose up -d --no-deps "$SERVICE"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["status"]=="ok" and data["schema_version"]=="1.1.0"'
python3 -m server.migrations verify --database data/cms.sqlite3 >/dev/null
validate_database data/cms.sqlite3 "production after stage 4"
curl -fsS http://127.0.0.1:8000/about | grep -Fq "$CONTACT_ADDRESS"
curl -fsS http://127.0.0.1:8000/about | grep -Fq "$CONTACT_PHONE"

require_equal "external site status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/")" "200"
require_equal "external school status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/school")" "200"
require_equal "external Pavel status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED$PAVEL_CLEAN")" "200"
require_equal "public-domain CMS status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/cms.html")" "404"
require_equal "external CMS status" "$(curl -ksS -o /dev/null -w '%{http_code}' 'https://cms.temple.gbvolkoff.name:8443/cms.html')" "200"
require_equal "external health status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/api/health")" "200"
curl -ksS "$PUBLIC_BASE_URL_EXPECTED/about" | grep -Fq "$CONTACT_ADDRESS"
curl -ksS "$PUBLIC_BASE_URL_EXPECTED/about" | grep -Fq "$CONTACT_PHONE"

python3 -m server.baseline report \
  --database data/cms.sqlite3 \
  --media-dir data/media \
  --git-sha "$CURRENT_SHA" \
  --tag "stage4-public-sections-from-cms" \
  --baseline-tag-sha "$CURRENT_SHA" \
  --image-id "$NEW_IMAGE_ID" \
  --env-file .env \
  --output "$POST_REPORT"
chmod 600 "$POST_REPORT"
DEPLOYMENT_SECONDS=$(( $(date +%s) - DEPLOYMENT_STARTED ))

echo "STAGE4_APPLIED=true"
echo "BACKUP_ID=$BACKUP_ID"
echo "IMPLEMENTATION_SHA=$CURRENT_SHA"
echo "IMAGE_ID=$NEW_IMAGE_ID"
echo "SERVER_ARCHIVE_SHA256=$SERVER_SHA256"
echo "PAVEL_CLEAN_URL=$PUBLIC_BASE_URL_EXPECTED$PAVEL_CLEAN"
echo "POST_REPORT=$POST_REPORT"
echo "DEPLOYMENT_SECONDS=$DEPLOYMENT_SECONDS"
