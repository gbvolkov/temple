#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/temple-backups}"
BACKUP_ID="${1:-${BACKUP_ID:-}}"
LOCAL_BACKUP_SHA256="${LOCAL_BACKUP_SHA256:-}"
SERVICE="cms"
DEPLOYMENT_STAGE="${DEPLOYMENT_STAGE:-4}"
EXPECTED_CONTENT_SCHEMA="${EXPECTED_CONTENT_SCHEMA:-1.1.0}"
DEPLOYMENT_TAG="${DEPLOYMENT_TAG:-stage4-public-sections-from-cms}"
DEPLOYMENT_ENTRYPOINT="${DEPLOYMENT_ENTRYPOINT:-$0}"
TEST_PORT="${TEST_PORT:-18004}"
PAVEL_ID="56871da9-3f57-4ff0-b405-3127668f7cad"
PUBLIC_BASE_URL_EXPECTED="https://temple.gbvolkoff.name:8443"
SOURCE_SCHEMA="${SOURCE_SCHEMA:-4}"
EXPECTED_SCHEMA="${EXPECTED_SCHEMA:-4}"
EXPECTED_REDIRECTS="1606"

case "$BACKUP_ID" in
  baseline-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Usage: LOCAL_BACKUP_SHA256=<sha256> $DEPLOYMENT_ENTRYPOINT baseline-YYYYMMDDTHHMMSSZ" >&2; exit 1 ;;
esac

for command_name in git curl sqlite3 tar sha256sum python3 sudo docker grep seq date awk tr find; do
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
TEST_ROOT="$BACKUP_ROOT/stage${DEPLOYMENT_STAGE}-test-$BACKUP_ID"
TEST_DATA="$TEST_ROOT/$BACKUP_ID"
TEST_CONTAINER="temple-stage${DEPLOYMENT_STAGE}-test-${BACKUP_ID//[^a-zA-Z0-9]/-}"
POST_REPORT="$BACKUP_ROOT/$BACKUP_ID-stage${DEPLOYMENT_STAGE}-post-report.json"

remove_test_root() {
  case "$TEST_ROOT" in
    "$BACKUP_ROOT"/stage"$DEPLOYMENT_STAGE"-test-baseline-*)
      if [[ -e "$TEST_ROOT" ]]; then
        sudo rm -rf -- "$TEST_ROOT"
      fi
      ;;
    *)
      echo "Refusing to remove unsafe test path: $TEST_ROOT" >&2
      return 1
      ;;
  esac
}

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
import ipaddress
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

validate_stage8_environment() {
  python3 - "$1" <<'PY'
import ipaddress
import sys
from pathlib import Path

values = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    values[name.strip()] = value.strip().strip('"').strip("'")
core_required = (
    "SUBMISSION_IP_HASH_SECRET", "SUBMISSION_TRUSTED_PROXY_NETWORKS",
)
smtp_fields = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
    "SMTP_SECURITY", "SUBMISSION_NOTIFY_TO",
)
if any(not values.get(name) for name in core_required):
    raise SystemExit(1)
if any("replace-with" in values[name].lower() for name in core_required):
    raise SystemExit(1)
if len(values["SUBMISSION_IP_HASH_SECRET"]) < 32:
    raise SystemExit(1)
try:
    for network in values["SUBMISSION_TRUSTED_PROXY_NETWORKS"].split(","):
        ipaddress.ip_network(network.strip(), strict=False)
except ValueError:
    raise SystemExit(1)
configured = [bool(values.get(name)) for name in smtp_fields]
if any(configured) and not all(configured):
    raise SystemExit(1)
if not any(configured):
    raise SystemExit(0)
if any("replace-with" in values[name].lower() for name in smtp_fields):
    raise SystemExit(1)
if values["SMTP_SECURITY"].lower() not in {"starttls", "ssl"}:
    raise SystemExit(1)
if "@" not in values["SMTP_FROM"] or any(
    "@" not in item.strip() for item in values["SUBMISSION_NOTIFY_TO"].split(",")
):
    raise SystemExit(1)
try:
    port = int(values["SMTP_PORT"])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if 1 <= port <= 65535 else 1)
PY
}

stage8_email_enabled() {
  python3 - "$1" <<'PY'
import sys
from pathlib import Path

values = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
smtp_fields = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
    "SMTP_SECURITY", "SUBMISSION_NOTIFY_TO",
)
raise SystemExit(0 if all(values.get(name) for name in smtp_fields) else 1)
PY
}

validate_database() {
  local database="$1" label="$2"
  local expected_schema="${3:-$EXPECTED_SCHEMA}"
  test "$(database_value "$database" 'PRAGMA quick_check;')" = "ok"
  test -z "$(database_value "$database" 'PRAGMA foreign_key_check;')"
  require_equal "$label schema" "$(database_value "$database" 'SELECT COALESCE(MAX(version),0) FROM schema_migrations;')" "$expected_schema"
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
  if (( expected_schema >= 7 )); then
    require_equal "$label submission table" "$(database_value "$database" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='submissions';")" "1"
    require_equal "$label outbox table" "$(database_value "$database" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='notification_outbox';")" "1"
    require_equal "$label submission events table" "$(database_value "$database" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='submission_events';")" "1"
  fi
  if (( expected_schema >= 8 )); then
    require_equal "$label FTS5 table" "$(database_value "$database" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='content_search' AND lower(sql) LIKE '%fts5%';")" "1"
    local expected_searchable indexed_searchable stale_searchable
    expected_searchable="$(database_value "$database" "SELECT COUNT(*) FROM contents WHERE published_version IS NOT NULL AND status NOT IN ('archived','trash') AND content_type IN ('news','page','parish_section','clergy','gallery','service','leaflet_issue','video','site_contact');")"
    indexed_searchable="$(database_value "$database" "SELECT COUNT(*) FROM content_search;")"
    require_equal "$label FTS5 publications" "$indexed_searchable" "$expected_searchable"
    stale_searchable="$(database_value "$database" "SELECT COUNT(*) FROM content_search s LEFT JOIN contents c ON c.id=s.content_id AND CAST(c.published_version AS TEXT)=s.published_version WHERE c.id IS NULL OR c.status IN ('archived','trash');")"
    require_equal "$label stale FTS5 rows" "$stale_searchable" "0"
  fi
}

CONTENTS="$(report_value database.metrics.contents)"
REVISIONS="$(report_value database.metrics.revisions)"
PUBLISHED="$(report_value database.by_status.published)"
REVIEW_REQUIRED="$(report_value database.review_required)"
BACKUP_USERS="$(report_value database.metrics.users)"
CURRENT_SHA="$(git rev-parse HEAD)"
BACKUP_SHA="$(report_value source.git_sha)"

require_equal "deployment branch" "$(git branch --show-current)" "main"
require_equal "origin/main SHA" "$(git rev-parse origin/main)" "$CURRENT_SHA"
git merge-base --is-ancestor "$BACKUP_SHA" "$CURRENT_SHA" || {
  echo "The backup checkout $BACKUP_SHA is not an ancestor of deployment $CURRENT_SHA" >&2
  exit 1
}
require_equal "backup schema version" "$(report_value database.schema_version)" "$SOURCE_SCHEMA"
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
if (( DEPLOYMENT_STAGE >= 8 )); then
  validate_stage8_environment .env || {
    echo "Stage 8 requires a valid HMAC secret and proxy networks; SMTP must be either complete or fully disabled" >&2
    exit 1
  }
  if stage8_email_enabled .env; then
    STAGE8_EMAIL_MODE="enabled"
  else
    STAGE8_EMAIL_MODE="disabled"
  fi
fi
validate_database data/cms.sqlite3 "production before stage $DEPLOYMENT_STAGE" "$SOURCE_SCHEMA"

CONTACT_ADDRESS="$(database_value data/cms.sqlite3 "SELECT json_extract(r.snapshot_json,'$.data.address') FROM contents c JOIN revisions r ON r.content_id=c.id AND r.version=c.published_version WHERE c.content_type='site_contact' AND c.status NOT IN ('archived','trash') LIMIT 1;")"
CONTACT_PHONE="$(database_value data/cms.sqlite3 "SELECT json_extract(r.snapshot_json,'$.data.phone') FROM contents c JOIN revisions r ON r.content_id=c.id AND r.version=c.published_version WHERE c.content_type='site_contact' AND c.status NOT IN ('archived','trash') LIMIT 1;")"
test -n "$CONTACT_ADDRESS" || { echo "Published site_contact.address is required before stage 4" >&2; exit 1; }
test -n "$CONTACT_PHONE" || { echo "Published site_contact.phone is required before stage 4" >&2; exit 1; }

if [[ -e "$TEST_ROOT" ]]; then
  echo "Removing stale disposable restore: $TEST_ROOT"
  remove_test_root
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

TEST_ADMIN_CREDENTIALS="$TEST_ROOT/stage5-test-admin.json"
if (( DEPLOYMENT_STAGE >= 5 )); then
  # Create an isolated administrator in the disposable restored database.
  # This avoids depending on the real administrator password from production.
  python3 - "$TEST_DATA/data/cms.sqlite3" "$TEST_ADMIN_CREDENTIALS" <<'PY'
import base64
import hashlib
import json
import secrets
import sqlite3
import sys
import uuid
from datetime import UTC, datetime

database_path, credentials_path = sys.argv[1:]
username = f"stage5-rollout-{uuid.uuid4()}"
password = secrets.token_urlsafe(32)
salt = secrets.token_bytes(16)
iterations = 260_000
digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
encoded = "pbkdf2_sha256${}${}${}".format(
    iterations,
    base64.urlsafe_b64encode(salt).decode(),
    base64.urlsafe_b64encode(digest).decode(),
)
with sqlite3.connect(database_path) as connection:
    connection.execute(
        "INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES(?,?,?,?,1,?)",
        (str(uuid.uuid4()), username, encoded, "admin", datetime.now(UTC).isoformat()),
    )
with open(credentials_path, "w", encoding="utf-8") as output:
    json.dump({"username": username, "password": password}, output)
PY
  chmod 600 "$TEST_ADMIN_CREDENTIALS"
fi

TEST_STARTED=0
cleanup() {
  local exit_code=$?
  local cleanup_code=0
  set +e
  if [[ "$TEST_STARTED" -eq 1 ]]; then
    sudo docker stop "$TEST_CONTAINER" >/dev/null 2>&1 || true
  fi
  remove_test_root || cleanup_code=$?
  if [[ "$exit_code" -eq 0 && "$cleanup_code" -ne 0 ]]; then
    exit_code="$cleanup_code"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

TEST_ENV_ARGS=()
if (( DEPLOYMENT_STAGE >= 8 )); then
  validate_stage8_environment "$TEST_DATA/.env" || {
    echo "The restored .env does not contain complete stage 8 notification settings" >&2
    exit 1
  }
  # Never send restored-copy smoke submissions to the real recipient.
  TEST_ENV_ARGS=(-e SUBMISSION_NOTIFY_TO=)
fi

sudo docker run -d --rm \
  --name "$TEST_CONTAINER" \
  --env-file "$TEST_DATA/.env" \
  "${TEST_ENV_ARGS[@]}" \
  -p "127.0.0.1:$TEST_PORT:8000" \
  -v "$TEST_DATA/data:/data" \
  "$NEW_IMAGE_ID" >/dev/null
TEST_STARTED=1
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$TEST_PORT/api/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$TEST_PORT/api/health" | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["status"]=="ok" and data["schema_version"]==sys.argv[1]' "$EXPECTED_CONTENT_SCHEMA"
if [[ "$DEPLOYMENT_STAGE" == "6" ]]; then
  sudo docker exec "$TEST_CONTAINER" python -m server.media_library index \
    --database /data/cms.sqlite3 \
    --media-dir /data/media \
    --missing-report /app/outputs/missing-legacy-media.csv >/dev/null
fi
python3 -m server.migrations verify --database "$TEST_DATA/data/cms.sqlite3" >/dev/null
validate_database "$TEST_DATA/data/cms.sqlite3" "restored stage $DEPLOYMENT_STAGE image"
if (( DEPLOYMENT_STAGE >= 6 )); then
  TEST_MEDIA_FILES="$(find "$TEST_DATA/data/media" -type f ! -path '*/.*' | wc -l | tr -d ' ')"
  require_equal "restored media index" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM media WHERE status='ready';")" "$TEST_MEDIA_FILES"
  require_equal "restored invalid media" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM media WHERE status!='ready';")" "0"
  require_equal "restored missing legacy queue" "$(database_value "$TEST_DATA/data/cms.sqlite3" 'SELECT COUNT(*) FROM missing_media_issues;')" "201"
fi
if [[ "$DEPLOYMENT_STAGE" == "7" ]]; then
  require_equal "restored users after isolated admin" "$(database_value "$TEST_DATA/data/cms.sqlite3" 'SELECT COUNT(*) FROM users;')" "$((BACKUP_USERS + 1))"
  python3 scripts/stage7_restore_smoke.py "$TEST_PORT" "$TEST_ADMIN_CREDENTIALS"
fi
if (( DEPLOYMENT_STAGE >= 8 )); then
  python3 scripts/stage8_restore_smoke.py "$TEST_PORT" "$TEST_ADMIN_CREDENTIALS" "$TEST_DATA/data/cms.sqlite3"
fi
if [[ "$DEPLOYMENT_STAGE" == "9" ]]; then
  python3 scripts/stage9_restore_smoke.py "$TEST_PORT" "$TEST_ADMIN_CREDENTIALS" "$TEST_DATA/data/cms.sqlite3"
  python3 -m server.search verify --database "$TEST_DATA/data/cms.sqlite3" >/dev/null
fi

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

if [[ "$DEPLOYMENT_STAGE" == "5" ]]; then
  # Exercise draft creation and the exact server preview only on the disposable restore.
  # Credentials are read in-process and are never written to the terminal or report.
  python3 - "$TEST_PORT" "$TEST_ADMIN_CREDENTIALS" "$PAVEL_CLEAN" <<'PY'
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

port, credentials_path, pavel_path = sys.argv[1:]
with open(credentials_path, encoding="utf-8") as source:
    credentials = json.load(source)
username = credentials["username"]
password = credentials["password"]

base = f"http://127.0.0.1:{port}"
cookie_jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(cookie_jar)
)
session_cookie = ""

def request(path, *, method="GET", payload=None, csrf=""):
    headers = {}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if session_cookie:
        headers["Cookie"] = f"cms_session={session_cookie}"
    call = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with opener.open(call, timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")

status, raw = request(
    "/api/admin/login", method="POST",
    payload={"username": username, "password": password},
)
assert status == 200
csrf = json.loads(raw)["csrf_token"]
session_cookie = next(cookie.value for cookie in cookie_jar if cookie.name == "cms_session")
draft_payload = {
    "content_type": "page",
    "title": "Проверка редактора этапа 5",
    "data": {
        "body": [{
            "id": "stage5-rollout-paragraph",
            "type": "paragraph",
            "data": {"runs": [{"text": "Одноразовый черновик восстановленной копии", "marks": ["bold"]}]},
        }],
        "related_content": [],
    },
}
status, raw = request("/api/admin/contents", method="POST", payload=draft_payload, csrf=csrf)
assert status == 201
draft = json.loads(raw)
assert draft["status"] == "draft" and draft["is_public"] is False
status, _ = request(f"/pages/{draft['slug']}")
assert status == 404
status, preview = request(
    "/api/admin/content-preview", method="POST", csrf=csrf,
    payload={
        "content_id": draft["id"], "content_type": "page", "title": draft["title"],
        "slug": draft["slug"], "data": draft["data"],
    },
)
assert status == 200
assert "<strong>Одноразовый черновик восстановленной копии</strong>" in preview
status, _ = request(pavel_path)
assert status == 200
PY
fi

sudo docker stop "$TEST_CONTAINER" >/dev/null
TEST_STARTED=0
remove_test_root
trap - EXIT

# Production is changed only after the restored copy passes every check.
validate_database data/cms.sqlite3 "production immediately before deployment" "$SOURCE_SCHEMA"
DEPLOYMENT_STARTED="$(date +%s)"
sudo docker compose up -d --no-deps "$SERVICE"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health | python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["status"]=="ok" and data["schema_version"]==sys.argv[1]' "$EXPECTED_CONTENT_SCHEMA"
if [[ "$DEPLOYMENT_STAGE" == "6" ]]; then
  sudo docker compose exec -T "$SERVICE" python -m server.media_library index \
    --database /data/cms.sqlite3 \
    --media-dir /data/media \
    --missing-report /app/outputs/missing-legacy-media.csv >/dev/null
fi
python3 -m server.migrations verify --database data/cms.sqlite3 >/dev/null
validate_database data/cms.sqlite3 "production after stage $DEPLOYMENT_STAGE"
if [[ "$DEPLOYMENT_STAGE" == "9" ]]; then
  python3 -m server.search verify --database data/cms.sqlite3 >/dev/null
fi
if (( DEPLOYMENT_STAGE >= 6 )); then
  PRODUCTION_MEDIA_FILES="$(find data/media -type f ! -path '*/.*' | wc -l | tr -d ' ')"
  require_equal "production media index" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM media WHERE status='ready';")" "$PRODUCTION_MEDIA_FILES"
  require_equal "production invalid media" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM media WHERE status!='ready';")" "0"
  require_equal "production missing legacy queue" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM missing_media_issues;')" "201"
fi
if [[ "$DEPLOYMENT_STAGE" == "7" ]]; then
  require_equal "production users" "$(database_value data/cms.sqlite3 'SELECT COUNT(*) FROM users;')" "$BACKUP_USERS"
  require_equal "production user event table" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='user_events';")" "1"
fi
if [[ "$DEPLOYMENT_STAGE" == "8" ]]; then
  require_equal "production submission table" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='submissions';")" "1"
  require_equal "production outbox table" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='notification_outbox';")" "1"
  require_equal "production submission events table" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='submission_events';")" "1"

  # Submit one production control note through the public API. When SMTP is
  # enabled, require delivery; otherwise require a durable pending outbox row.
  # The control note is then closed as spam for the 30-day retention rule.
  STAGE8_CONTROL_REFERENCE="$(python3 - <<'PY'
import json
import secrets
import string
import urllib.request

name = "Stage " + "".join(secrets.choice(string.ascii_letters) for _ in range(12))
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/public/submissions/prayer-note",
    data=json.dumps({
        "remembrance_type": "health", "names": [name], "website": "",
    }).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=15) as response:
    result = json.load(response)
if response.status != 201 or not result.get("accepted"):
    raise SystemExit(1)
reference = result.get("reference_code", "")
if not reference.startswith("Z-"):
    raise SystemExit(1)
print(reference)
PY
)"
  STAGE8_CONTROL_ID="$(database_value data/cms.sqlite3 "SELECT id FROM submissions WHERE reference_code='$STAGE8_CONTROL_REFERENCE';")"
  [[ -n "$STAGE8_CONTROL_ID" ]] || { echo "Stage 8 control submission was not persisted" >&2; exit 1; }

  if [[ "$STAGE8_EMAIL_MODE" == "enabled" ]]; then
    DELIVERY_STATUS=""
    for _ in $(seq 1 100); do
      DELIVERY_STATUS="$(database_value data/cms.sqlite3 "SELECT status FROM notification_outbox WHERE submission_id='$STAGE8_CONTROL_ID';")"
      [[ "$DELIVERY_STATUS" == "sent" || "$DELIVERY_STATUS" == "failed" ]] && break
      sleep 1
    done
    require_equal "stage 8 control notification delivery" "$DELIVERY_STATUS" "sent"
  else
    require_equal "stage 8 disabled notification queue" "$(database_value data/cms.sqlite3 "SELECT status FROM notification_outbox WHERE submission_id='$STAGE8_CONTROL_ID';")" "pending"
  fi

  python3 - data/cms.sqlite3 "$STAGE8_CONTROL_ID" <<'PY'
import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime

database, submission_id = sys.argv[1:]
now = datetime.now(UTC).isoformat(timespec="seconds")
with sqlite3.connect(database) as connection:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT status FROM submissions WHERE id=?", (submission_id,)
    ).fetchone()
    if row is None:
        raise SystemExit(1)
    if row[0] != "spam":
        if row[0] not in {"new", "in_progress"}:
            raise SystemExit(1)
        connection.execute(
            """UPDATE submissions SET status='spam',version=version+1,handled_by=NULL,
               updated_at=?,closed_at=? WHERE id=?""",
            (now, now, submission_id),
        )
        connection.execute(
            """INSERT INTO submission_events(
                 id,submission_id,actor_id,action,from_status,to_status,details_json,created_at
               ) VALUES(?,?,NULL,'status_changed',?,'spam',?,?)""",
            (str(uuid.uuid4()), submission_id, row[0], json.dumps({"control": True}), now),
        )
PY
fi
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
if [[ "$DEPLOYMENT_STAGE" == "9" ]]; then
  require_equal "external search status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/search")" "200"
  require_equal "external sitemap status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/sitemap.xml")" "200"
  require_equal "external robots status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/robots.txt")" "200"
  require_equal "external RSS status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED/rss.xml")" "200"
  PAVEL_SOCIAL="/social-preview/content/$PAVEL_ID/v8.jpg"
  require_equal "external social preview status" "$(curl -ksS -o /dev/null -w '%{http_code}' "$PUBLIC_BASE_URL_EXPECTED$PAVEL_SOCIAL")" "200"
  curl -ksS "$PUBLIC_BASE_URL_EXPECTED$PAVEL_CLEAN" | grep -Fq "<link rel=\"canonical\" href=\"$PUBLIC_BASE_URL_EXPECTED$PAVEL_CLEAN\">"
  curl -ksS "$PUBLIC_BASE_URL_EXPECTED/robots.txt" | grep -Fq "Sitemap: $PUBLIC_BASE_URL_EXPECTED/sitemap.xml"
fi

python3 -m server.baseline report \
  --database data/cms.sqlite3 \
  --media-dir data/media \
  --git-sha "$CURRENT_SHA" \
  --tag "$DEPLOYMENT_TAG" \
  --baseline-tag-sha "$CURRENT_SHA" \
  --image-id "$NEW_IMAGE_ID" \
  --env-file .env \
  --output "$POST_REPORT"
chmod 600 "$POST_REPORT"
DEPLOYMENT_SECONDS=$(( $(date +%s) - DEPLOYMENT_STARTED ))

echo "STAGE${DEPLOYMENT_STAGE}_APPLIED=true"
echo "BACKUP_ID=$BACKUP_ID"
echo "IMPLEMENTATION_SHA=$CURRENT_SHA"
echo "IMAGE_ID=$NEW_IMAGE_ID"
echo "SERVER_ARCHIVE_SHA256=$SERVER_SHA256"
echo "PAVEL_CLEAN_URL=$PUBLIC_BASE_URL_EXPECTED$PAVEL_CLEAN"
echo "POST_REPORT=$POST_REPORT"
if [[ "$DEPLOYMENT_STAGE" == "8" ]]; then
  echo "CONTROL_SUBMISSION=$STAGE8_CONTROL_REFERENCE"
  echo "SMTP_MODE=$STAGE8_EMAIL_MODE"
fi
echo "DEPLOYMENT_SECONDS=$DEPLOYMENT_SECONDS"
