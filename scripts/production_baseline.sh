#!/usr/bin/env bash
set -Eeuo pipefail

BASELINE_TAG="baseline-before-completion-20260718"
PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/temple-backups}"
MIN_FREE_KB=$((8 * 1024 * 1024))
SERVICE="cms"
RESTORE_PORT="18000"

for command_name in git curl rsync sqlite3 tar sha256sum python3 sudo docker df find stat; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done

cd "$PROJECT_DIR"
git rev-parse --verify "${BASELINE_TAG}^{}" >/dev/null
BASELINE_SHA="$(git rev-parse "${BASELINE_TAG}^{}")"
CURRENT_SHA="$(git rev-parse HEAD)"
git merge-base --is-ancestor "$BASELINE_SHA" "$CURRENT_SHA" || {
  echo "The baseline tag is not an ancestor of the current checkout" >&2
  exit 1
}
BACKUP_ID="${BACKUP_ID:-baseline-$(date -u +%Y%m%dT%H%M%SZ)}"

case "$BACKUP_ID" in
  baseline-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Unsafe BACKUP_ID: $BACKUP_ID" >&2; exit 1 ;;
esac

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
STAGING="$BACKUP_ROOT/$BACKUP_ID"
ARCHIVE="$BACKUP_ROOT/$BACKUP_ID.tar.gz"
ARCHIVE_CHECKSUM="$ARCHIVE.sha256"
REPORT_COPY="$BACKUP_ROOT/$BACKUP_ID-baseline-report.json"
RESTORE_REPORT="$BACKUP_ROOT/$BACKUP_ID-restore-verification.json"
ARCHIVE_CONTENTS="$BACKUP_ROOT/$BACKUP_ID-archive-contents.txt"
RESTORE_ROOT="$BACKUP_ROOT/restore-$BACKUP_ID"
RESTORE_CONTAINER="temple-restore-${BACKUP_ID//[^a-zA-Z0-9]/-}"

if [[ -e "$STAGING" || -e "$ARCHIVE" || -e "$RESTORE_ROOT" ]]; then
  echo "Backup destination already exists for $BACKUP_ID" >&2
  exit 1
fi

FREE_KB="$(df -Pk "$BACKUP_ROOT" | awk 'NR==2 {print $4}')"
if [[ -z "$FREE_KB" || "$FREE_KB" -lt "$MIN_FREE_KB" ]]; then
  echo "At least 8 GiB free space is required in $BACKUP_ROOT" >&2
  exit 1
fi

CONTAINER_ID="$(sudo docker compose ps -q "$SERVICE")"
if [[ -z "$CONTAINER_ID" ]]; then
  echo "CMS container is not running" >&2
  exit 1
fi
HEALTH_STATUS="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$CONTAINER_ID")"
if [[ "$HEALTH_STATUS" != "healthy" ]]; then
  echo "CMS container health is $HEALTH_STATUS, expected healthy" >&2
  exit 1
fi
sudo docker compose ps
curl -fsS http://127.0.0.1:8000/api/health >/dev/null
sudo docker compose logs --tail=100 "$SERVICE" >"$BACKUP_ROOT/$BACKUP_ID-preflight.log"
test -r data/cms.sqlite3
test -d data/media
test -w data
test -w data/media
test -r .env
{
  stat -c 'data: %A %U:%G %n' data
  stat -c 'database: %A %U:%G %n' data/cms.sqlite3
  stat -c 'media: %A %U:%G %n' data/media
  stat -c 'env: %A %U:%G %n' .env
  stat -c 'backup-root: %A %U:%G %n' "$BACKUP_ROOT"
  df -h "$BACKUP_ROOT"
} >"$BACKUP_ROOT/$BACKUP_ID-preflight-filesystem.txt"
chmod 600 "$BACKUP_ROOT/$BACKUP_ID-preflight.log" "$BACKUP_ROOT/$BACKUP_ID-preflight-filesystem.txt"

mkdir -p "$STAGING/data/media"
chmod 700 "$STAGING"
rsync -a --delete data/media/ "$STAGING/data/media/"

IMAGE_ID="$(sudo docker inspect --format '{{.Image}}' "$CONTAINER_ID")"
if [[ -z "$IMAGE_ID" ]]; then
  echo "Unable to determine the running image ID" >&2
  exit 1
fi

CMS_STOPPED=0
RESTORE_STARTED=0
cleanup() {
  exit_code=$?
  if [[ "$RESTORE_STARTED" -eq 1 ]]; then
    sudo docker stop "$RESTORE_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [[ "$CMS_STOPPED" -eq 1 ]]; then
    sudo docker compose up -d "$SERVICE" >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT

DOWNTIME_STARTED="$(date +%s)"
sudo docker compose stop "$SERVICE"
CMS_STOPPED=1

sqlite3 data/cms.sqlite3 "PRAGMA wal_checkpoint(TRUNCATE);" >"$STAGING/sqlite-checkpoint.txt"
QUICK_CHECK="$(sqlite3 -readonly data/cms.sqlite3 "PRAGMA quick_check;")"
FOREIGN_KEY_ERRORS="$(sqlite3 -readonly data/cms.sqlite3 "PRAGMA foreign_key_check;")"
printf '%s\n' "$QUICK_CHECK" >"$STAGING/sqlite-quick-check.txt"
printf '%s' "$FOREIGN_KEY_ERRORS" >"$STAGING/sqlite-foreign-key-check.txt"
if [[ "$QUICK_CHECK" != "ok" || -n "$FOREIGN_KEY_ERRORS" ]]; then
  echo "SQLite integrity validation failed; the service will be restarted" >&2
  exit 1
fi

sqlite3 data/cms.sqlite3 ".backup '$STAGING/data/cms.sqlite3'"
rsync -a --delete data/media/ "$STAGING/data/media/"

for artifact in legacy-crawl-checkpoint.json legacy-media-manifest.json full-import-plan.json; do
  if [[ -f "data/$artifact" ]]; then
    install -m 600 "data/$artifact" "$STAGING/data/$artifact"
  fi
done
install -m 600 .env "$STAGING/.env"
install -m 600 docker-compose.yml "$STAGING/docker-compose.yml"
install -m 600 Dockerfile "$STAGING/Dockerfile"
printf '%s\n' "$BASELINE_SHA" >"$STAGING/baseline-tag-sha.txt"
printf '%s\n' "$BASELINE_TAG" >"$STAGING/git-tag.txt"
printf '%s\n' "$CURRENT_SHA" >"$STAGING/checkout-git-sha.txt"
printf '%s\n' "$IMAGE_ID" >"$STAGING/docker-image-id.txt"
sudo docker compose ps >"$STAGING/docker-compose-ps-before.txt"

sudo docker compose up -d "$SERVICE"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    CMS_STOPPED=0
    break
  fi
  sleep 1
done
if [[ "$CMS_STOPPED" -eq 1 ]]; then
  echo "CMS did not become healthy after restart" >&2
  exit 1
fi
DOWNTIME_SECONDS=$(( $(date +%s) - DOWNTIME_STARTED ))
printf '%s\n' "$DOWNTIME_SECONDS" >"$STAGING/downtime-seconds.txt"
curl -fsS http://127.0.0.1:8000/ >/dev/null
sudo docker compose ps >"$STAGING/docker-compose-ps-after.txt"

python3 -m server.baseline media-manifest \
  --media-dir "$STAGING/data/media" \
  --output "$STAGING/media-manifest.jsonl" >"$STAGING/media-manifest-summary.json"

REPORT_ARGS=(
  report
  --database "$STAGING/data/cms.sqlite3"
  --media-dir "$STAGING/data/media"
  --git-sha "$CURRENT_SHA"
  --tag "$BASELINE_TAG"
  --baseline-tag-sha "$BASELINE_SHA"
  --image-id "$IMAGE_ID"
  --env-file "$STAGING/.env"
  --output "$STAGING/baseline-report.json"
)
for artifact in "$STAGING/data/legacy-crawl-checkpoint.json" "$STAGING/data/legacy-media-manifest.json" "$STAGING/data/full-import-plan.json"; do
  if [[ -f "$artifact" ]]; then
    REPORT_ARGS+=(--artifact "$artifact")
  fi
done
python3 -m server.baseline "${REPORT_ARGS[@]}"
install -m 600 "$STAGING/baseline-report.json" "$REPORT_COPY"

tar -C "$BACKUP_ROOT" -czf "$ARCHIVE" "$BACKUP_ID"
chmod 600 "$ARCHIVE"
(
  cd "$BACKUP_ROOT"
  sha256sum "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE_CHECKSUM")"
)
chmod 600 "$ARCHIVE_CHECKSUM" "$REPORT_COPY"
(
  cd "$BACKUP_ROOT"
  sha256sum -c "$(basename "$ARCHIVE_CHECKSUM")"
)
tar -tzf "$ARCHIVE" >"$ARCHIVE_CONTENTS"
chmod 600 "$ARCHIVE_CONTENTS"

mkdir -p "$RESTORE_ROOT"
chmod 700 "$RESTORE_ROOT"
tar -C "$RESTORE_ROOT" -xzf "$ARCHIVE"
RESTORED="$RESTORE_ROOT/$BACKUP_ID"
python3 -m server.baseline verify \
  --database "$RESTORED/data/cms.sqlite3" \
  --media-dir "$RESTORED/data/media" \
  --report "$RESTORED/baseline-report.json" \
  --media-manifest "$RESTORED/media-manifest.jsonl" \
  --output "$RESTORE_REPORT"
chmod 600 "$RESTORE_REPORT"

if command -v ss >/dev/null 2>&1 && ss -ltn | grep -q ":$RESTORE_PORT "; then
  echo "Restore test port $RESTORE_PORT is already in use" >&2
  exit 1
fi
sudo docker run -d --rm \
  --name "$RESTORE_CONTAINER" \
  --env-file "$RESTORED/.env" \
  -p "127.0.0.1:$RESTORE_PORT:8000" \
  -v "$RESTORED/data:/data" \
  "$IMAGE_ID" >/dev/null
RESTORE_STARTED=1
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$RESTORE_PORT/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:$RESTORE_PORT/api/health" >/dev/null
curl -fsS "http://127.0.0.1:$RESTORE_PORT/api/public/content?limit=1" >/dev/null
sudo docker stop "$RESTORE_CONTAINER" >/dev/null
RESTORE_STARTED=0

case "$RESTORE_ROOT" in
  "$BACKUP_ROOT"/restore-baseline-*) rm -rf -- "$RESTORE_ROOT" ;;
  *) echo "Refusing to remove unsafe restore path: $RESTORE_ROOT" >&2; exit 1 ;;
esac

if [[ "$DOWNTIME_SECONDS" -gt 300 ]]; then
  echo "Backup is valid, but downtime exceeded 300 seconds: $DOWNTIME_SECONDS" >&2
  exit 1
fi

case "$STAGING" in
  "$BACKUP_ROOT"/baseline-*) rm -rf -- "$STAGING" ;;
  *) echo "Refusing to remove unsafe staging path: $STAGING" >&2; exit 1 ;;
esac

echo "BACKUP_ID=$BACKUP_ID"
echo "ARCHIVE=$ARCHIVE"
echo "CHECKSUM=$ARCHIVE_CHECKSUM"
echo "REPORT=$REPORT_COPY"
echo "RESTORE_REPORT=$RESTORE_REPORT"
echo "DOWNTIME_SECONDS=$DOWNTIME_SECONDS"
echo "BASELINE_SHA=$BASELINE_SHA"
echo "CURRENT_SHA=$CURRENT_SHA"
