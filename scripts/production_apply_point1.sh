#!/usr/bin/env bash
set -Eeuo pipefail

BASELINE_TAG="baseline-before-completion-20260718"
PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/temple-backups}"
BACKUP_ID="${1:-${BACKUP_ID:-}}"
SERVICE="cms"
TEST_PORT="18001"
TARGET_ID="660f8f7c-1183-464d-b39c-f4df2579fd45"
TARGET_SLUG="o-hrame-novosti-prihoda-arhiv-novostey-2014-god-svyashhenstvo-eto-prizvanie-pamyati-arhimandrita-ioanna-krestyankina"

case "$BACKUP_ID" in
  baseline-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Usage: $0 baseline-YYYYMMDDTHHMMSSZ" >&2; exit 1 ;;
esac

for command_name in git curl sqlite3 tar sha256sum python3 sudo docker grep seq; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done

cd "$PROJECT_DIR"
ARCHIVE="$BACKUP_ROOT/$BACKUP_ID.tar.gz"
ARCHIVE_CHECKSUM="$ARCHIVE.sha256"
BASELINE_REPORT="$BACKUP_ROOT/$BACKUP_ID-baseline-report.json"
RESTORE_REPORT="$BACKUP_ROOT/$BACKUP_ID-restore-verification.json"
TEST_ROOT="$BACKUP_ROOT/apply-test-$BACKUP_ID"
TEST_CONTAINER="temple-apply-test-${BACKUP_ID//[^a-zA-Z0-9]/-}"
POST_REPORT="$BACKUP_ROOT/$BACKUP_ID-post-migration-report.json"

test -r "$ARCHIVE"
test -r "$ARCHIVE_CHECKSUM"
test -r "$BASELINE_REPORT"
test -r "$RESTORE_REPORT"
(
  cd "$BACKUP_ROOT"
  sha256sum -c "$(basename "$ARCHIVE_CHECKSUM")"
)
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

BASELINE_CONTENTS="$(report_value database.metrics.contents)"
BASELINE_REVISIONS="$(report_value database.metrics.revisions)"
BASELINE_PUBLISHED="$(report_value database.by_status.published)"
BASELINE_REVIEW_REQUIRED="$(report_value database.review_required)"
BASELINE_MEDIA_FILES="$(report_value media.files)"
BASELINE_MEDIA_BYTES="$(report_value media.size_bytes)"
EXPECTED_PUBLISHED_AFTER=$((BASELINE_PUBLISHED - 1))
EXPECTED_REVIEW_REQUIRED_AFTER=$((BASELINE_REVIEW_REQUIRED + 1))
EXPECTED_REVISIONS_AFTER=$((BASELINE_REVISIONS + 1))

database_value() {
  database="$1"
  query="$2"
  sqlite3 -readonly "$database" "$query"
}

other_published_ids() {
  database="$1"
  database_value "$database" "SELECT COALESCE(group_concat(id,'|'),'') FROM (SELECT id FROM contents WHERE status='published' AND id<>'$TARGET_ID' ORDER BY id);"
}

verify_live_matches_baseline() {
  require_equal "production content count" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM contents;")" "$BASELINE_CONTENTS"
  require_equal "production revision count" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM revisions;")" "$BASELINE_REVISIONS"
  require_equal "production published count" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM contents WHERE status='published';")" "$BASELINE_PUBLISHED"
  require_equal "production review-required count" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM contents WHERE migration_review_required=1;")" "$BASELINE_REVIEW_REQUIRED"
  require_equal "production target state" "$(database_value data/cms.sqlite3 "SELECT status||'|'||migration_review_required||'|'||version||'|'||slug FROM contents WHERE id='$TARGET_ID';")" "published|0|7|$TARGET_SLUG"
  require_equal "production schema migration table count" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations';")" "0"
  media_summary="$(python3 -c 'import pathlib,sys; files=[p for p in pathlib.Path(sys.argv[1]).rglob("*") if p.is_file()]; print(f"{len(files)}|{sum(p.stat().st_size for p in files)}")' data/media)"
  require_equal "production media summary" "$media_summary" "$BASELINE_MEDIA_FILES|$BASELINE_MEDIA_BYTES"
}

BASELINE_SHA="$(git rev-parse "${BASELINE_TAG}^{}")"
CURRENT_SHA="$(git rev-parse HEAD)"
git merge-base --is-ancestor "$BASELINE_SHA" "$CURRENT_SHA" || {
  echo "The baseline tag is not an ancestor of the current checkout" >&2
  exit 1
}
if ! git diff --quiet -- . ':(exclude)data/cms.sqlite3'; then
  echo "Tracked code/configuration changes are present; refusing production deployment" >&2
  exit 1
fi
python3 -m server.migrations verify --database data/cms.sqlite3 >/dev/null
python3 -m server.migrations up --dry-run --database data/cms.sqlite3
verify_live_matches_baseline

if [[ -e "$TEST_ROOT" ]]; then
  echo "Test restore destination already exists: $TEST_ROOT" >&2
  exit 1
fi
if command -v ss >/dev/null 2>&1 && ss -ltn | grep -q ":$TEST_PORT "; then
  echo "Migration test port $TEST_PORT is already in use" >&2
  exit 1
fi

sudo docker compose build "$SERVICE"
mapfile -t IMAGE_REFERENCES < <(sudo docker compose config --images)
if [[ "${#IMAGE_REFERENCES[@]}" -ne 1 ]]; then
  echo "Expected exactly one Compose image reference" >&2
  exit 1
fi
NEW_IMAGE_ID="$(sudo docker image inspect --format '{{.Id}}' "${IMAGE_REFERENCES[0]}")"
if [[ -z "$NEW_IMAGE_ID" ]]; then
  echo "Unable to determine the newly built image ID" >&2
  exit 1
fi

mkdir -p "$TEST_ROOT"
chmod 700 "$TEST_ROOT"
tar -C "$TEST_ROOT" -xzf "$ARCHIVE"
TEST_DATA="$TEST_ROOT/$BACKUP_ID"

TEST_STARTED=0
cleanup() {
  exit_code=$?
  if [[ "$TEST_STARTED" -eq 1 ]]; then
    sudo docker stop "$TEST_CONTAINER" >/dev/null 2>&1 || true
  fi
  case "$TEST_ROOT" in
    "$BACKUP_ROOT"/apply-test-baseline-*) rm -rf -- "$TEST_ROOT" ;;
    *) echo "Refusing to remove unsafe test path: $TEST_ROOT" >&2 ;;
  esac
  exit "$exit_code"
}
trap cleanup EXIT

python3 -m server.baseline verify \
  --database "$TEST_DATA/data/cms.sqlite3" \
  --media-dir "$TEST_DATA/data/media" \
  --report "$TEST_DATA/baseline-report.json" \
  --media-manifest "$TEST_DATA/media-manifest.jsonl" >/dev/null
TEST_OTHER_PUBLISHED_BEFORE="$(other_published_ids "$TEST_DATA/data/cms.sqlite3")"

sudo docker run -d --rm \
  --name "$TEST_CONTAINER" \
  --env-file "$TEST_DATA/.env" \
  -p "127.0.0.1:$TEST_PORT:8000" \
  -v "$TEST_DATA/data:/data" \
  "$NEW_IMAGE_ID" >/dev/null
TEST_STARTED=1
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$TEST_PORT/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$TEST_PORT/api/health" >/dev/null
python3 -m server.migrations verify --database "$TEST_DATA/data/cms.sqlite3" >/dev/null
require_equal "test content count" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM contents;")" "$BASELINE_CONTENTS"
require_equal "test revision count" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM revisions;")" "$EXPECTED_REVISIONS_AFTER"
require_equal "test published count" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM contents WHERE status='published';")" "$EXPECTED_PUBLISHED_AFTER"
require_equal "test review-required count" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM contents WHERE migration_review_required=1;")" "$EXPECTED_REVIEW_REQUIRED_AFTER"
require_equal "test target state" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT status||'|'||migration_review_required||'|'||version||'|'||COALESCE(published_at,'NULL') FROM contents WHERE id='$TARGET_ID';")" "draft|1|8|NULL"
require_equal "test preserved published IDs" "$(other_published_ids "$TEST_DATA/data/cms.sqlite3")" "$TEST_OTHER_PUBLISHED_BEFORE"
require_equal "test preserved published revision" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM revisions WHERE content_id='$TARGET_ID' AND version=7 AND json_extract(snapshot_json,'$.status')='published';")" "1"
require_equal "test system draft revision" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM revisions WHERE content_id='$TARGET_ID' AND version=8 AND actor_id IS NULL AND json_extract(snapshot_json,'$.status')='draft';")" "1"
require_equal "test applied migration count" "$(database_value "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM schema_migrations;")" "2"
require_equal "test published news API" "$(curl -fsS "http://127.0.0.1:$TEST_PORT/api/public/content?content_type=news")" "[]"
curl -fsS "http://127.0.0.1:$TEST_PORT/app.js" | grep -q 'Новости готовятся к публикации'
if curl -fsS "http://127.0.0.1:$TEST_PORT/app.js" | grep -q 'Фотовыставка памяти Святейшего Патриарха Тихона'; then
  echo "Demonstration news is still present in the public bundle" >&2
  exit 1
fi
sudo docker stop "$TEST_CONTAINER" >/dev/null
TEST_STARTED=0

case "$TEST_ROOT" in
  "$BACKUP_ROOT"/apply-test-baseline-*) rm -rf -- "$TEST_ROOT" ;;
  *) echo "Refusing to remove unsafe test path: $TEST_ROOT" >&2; exit 1 ;;
esac
trap - EXIT

verify_live_matches_baseline
LIVE_OTHER_PUBLISHED_BEFORE="$(other_published_ids data/cms.sqlite3)"
DEPLOYMENT_STARTED="$(date +%s)"
sudo docker compose up -d --no-deps "$SERVICE"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health >/dev/null
python3 -m server.migrations verify --database data/cms.sqlite3 >/dev/null
test "$(sqlite3 -readonly data/cms.sqlite3 "PRAGMA quick_check;")" = "ok"
test -z "$(sqlite3 -readonly data/cms.sqlite3 "PRAGMA foreign_key_check;")"
require_equal "production content count after migration" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM contents;")" "$BASELINE_CONTENTS"
require_equal "production revision count after migration" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM revisions;")" "$EXPECTED_REVISIONS_AFTER"
require_equal "production published count after migration" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM contents WHERE status='published';")" "$EXPECTED_PUBLISHED_AFTER"
require_equal "production review-required count after migration" "$(database_value data/cms.sqlite3 "SELECT COUNT(*) FROM contents WHERE migration_review_required=1;")" "$EXPECTED_REVIEW_REQUIRED_AFTER"
require_equal "production target state after migration" "$(database_value data/cms.sqlite3 "SELECT status||'|'||migration_review_required||'|'||version||'|'||COALESCE(published_at,'NULL') FROM contents WHERE id='$TARGET_ID';")" "draft|1|8|NULL"
require_equal "production preserved published IDs" "$(other_published_ids data/cms.sqlite3)" "$LIVE_OTHER_PUBLISHED_BEFORE"
require_equal "production published news API" "$(curl -fsS 'http://127.0.0.1:8000/api/public/content?content_type=news')" "[]"
curl -fsS http://127.0.0.1:8000/app.js | grep -q 'Новости готовятся к публикации'
if curl -fsS http://127.0.0.1:8000/app.js | grep -q 'Фотовыставка памяти Святейшего Патриарха Тихона'; then
  echo "Demonstration news is still present in the public bundle" >&2
  exit 1
fi

python3 -m server.baseline report \
  --database data/cms.sqlite3 \
  --media-dir data/media \
  --git-sha "$CURRENT_SHA" \
  --tag "$BASELINE_TAG" \
  --baseline-tag-sha "$BASELINE_SHA" \
  --image-id "$NEW_IMAGE_ID" \
  --env-file .env \
  --output "$POST_REPORT"
chmod 600 "$POST_REPORT"
DEPLOYMENT_SECONDS=$(( $(date +%s) - DEPLOYMENT_STARTED ))

echo "POINT1_APPLIED=true"
echo "BACKUP_ID=$BACKUP_ID"
echo "IMPLEMENTATION_SHA=$CURRENT_SHA"
echo "IMAGE_ID=$NEW_IMAGE_ID"
echo "POST_REPORT=$POST_REPORT"
echo "DEPLOYMENT_SECONDS=$DEPLOYMENT_SECONDS"
