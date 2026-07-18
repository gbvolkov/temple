#!/usr/bin/env bash
set -Eeuo pipefail

BASELINE_TAG="baseline-before-completion-20260718"
PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/temple-backups}"
BACKUP_ID="${1:-${BACKUP_ID:-}}"
SERVICE="cms"
TEST_PORT="18001"

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
test "$(sqlite3 -readonly "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM contents WHERE status='published';")" = "0"
test "$(sqlite3 -readonly "$TEST_DATA/data/cms.sqlite3" "SELECT COUNT(*) FROM contents WHERE migration_review_required=1;")" = "1757"
test "$(sqlite3 -readonly "$TEST_DATA/data/cms.sqlite3" "SELECT status||'|'||migration_review_required||'|'||version||'|'||COALESCE(published_at,'NULL') FROM contents WHERE id='660f8f7c-1183-464d-b39c-f4df2579fd45';")" = "draft|1|8|NULL"
test "$(curl -fsS "http://127.0.0.1:$TEST_PORT/api/public/content")" = "[]"
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
test "$(sqlite3 -readonly data/cms.sqlite3 "SELECT COUNT(*) FROM contents WHERE status='published';")" = "0"
test "$(sqlite3 -readonly data/cms.sqlite3 "SELECT COUNT(*) FROM contents WHERE migration_review_required=1;")" = "1757"
test "$(sqlite3 -readonly data/cms.sqlite3 "SELECT status||'|'||migration_review_required||'|'||version||'|'||COALESCE(published_at,'NULL') FROM contents WHERE id='660f8f7c-1183-464d-b39c-f4df2579fd45';")" = "draft|1|8|NULL"
test "$(curl -fsS http://127.0.0.1:8000/api/public/content)" = "[]"
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
