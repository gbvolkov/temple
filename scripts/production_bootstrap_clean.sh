#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
STATE_ROOT="${STATE_ROOT:-$PROJECT_DIR/.clean-deploy}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
SERVICE="${SERVICE:-cms}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"
EXPECTED_CONTENT_SCHEMA="${EXPECTED_CONTENT_SCHEMA:-1.6.0}"
VERIFY_EXTERNAL="${VERIFY_EXTERNAL:-0}"
BUNDLE_ARCHIVE="${1:-}"
BUNDLE_CHECKSUM="${2:-${BUNDLE_ARCHIVE}.sha256}"

usage() {
  echo "Usage: $0 /absolute/path/public-source-YYYYMMDDTHHMMSSZ.tar.gz [/absolute/path/archive.sha256]" >&2
  exit 2
}

[[ -n "$BUNDLE_ARCHIVE" ]] || usage
BUNDLE_ARCHIVE="$(readlink -f "$BUNDLE_ARCHIVE")"
BUNDLE_CHECKSUM="$(readlink -f "$BUNDLE_CHECKSUM")"
BUNDLE_NAME="$(basename "$BUNDLE_ARCHIVE")"
case "$BUNDLE_NAME" in
  public-source-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.tar.gz) ;;
  *) usage ;;
esac
DEPLOY_ID="${BUNDLE_NAME%.tar.gz}"

for command_name in git curl rsync sqlite3 tar sha256sum python3 sudo docker find readlink df awk install seq date; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done

test -r "$BUNDLE_ARCHIVE" || { echo "Bundle archive is not readable: $BUNDLE_ARCHIVE" >&2; exit 1; }
test -r "$BUNDLE_CHECKSUM" || { echo "Bundle checksum is not readable: $BUNDLE_CHECKSUM" >&2; exit 1; }
test -r "$ENV_FILE" || { echo "Production .env is not readable: $ENV_FILE" >&2; exit 1; }

cd "$PROJECT_DIR"
CURRENT_SHA="$(git rev-parse HEAD)"
test "$(git branch --show-current)" = "main" || { echo "Clean bootstrap requires branch main" >&2; exit 1; }
test "$(git rev-parse origin/main)" = "$CURRENT_SHA" || { echo "HEAD must match origin/main" >&2; exit 1; }
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Deployment checkout must be clean" >&2
  exit 1
fi
python3 scripts/validate_production_env.py "$ENV_FILE" >/dev/null

if [[ -n "$(sudo docker compose ps --all -q "$SERVICE")" ]]; then
  echo "The CMS service is already created; clean bootstrap refuses an existing deployment" >&2
  exit 1
fi
mkdir -p data
if [[ -n "$(find data -mindepth 1 ! -name .gitkeep -print -quit)" ]]; then
  echo "data/ is not empty; use the versioned upgrade/restore workflow instead" >&2
  exit 1
fi

mkdir -p "$STATE_ROOT"
chmod 700 "$STATE_ROOT"
ATTEMPT_ID="${ATTEMPT_ID:-attempt-$(date -u +%Y%m%dT%H%M%SZ)}"
case "$ATTEMPT_ID" in
  attempt-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Unsafe ATTEMPT_ID: $ATTEMPT_ID" >&2; exit 1 ;;
esac
BUNDLE_STATE="$STATE_ROOT/$DEPLOY_ID"
DEPLOY_ROOT="$BUNDLE_STATE/$ATTEMPT_ID"
SOURCE_DIR="$DEPLOY_ROOT/source"
STAGE_DATA="$DEPLOY_ROOT/stage-data"
REPORTS_DIR="$DEPLOY_ROOT/reports"
ORIGINAL_DATA="$DEPLOY_ROOT/original-data"
FAILED_DATA="$DEPLOY_ROOT/failed-data"
if [[ -e "$DEPLOY_ROOT" ]]; then
  echo "Deployment state already exists: $DEPLOY_ROOT" >&2
  exit 1
fi
FREE_KB="$(df -Pk "$STATE_ROOT" | awk 'NR==2 {print $4}')"
ARCHIVE_KB="$(python3 -c 'import pathlib,sys; print((pathlib.Path(sys.argv[1]).stat().st_size + 1023) // 1024)' "$BUNDLE_ARCHIVE")"
MIN_FREE_KB=$((10 * 1024 * 1024))
DYNAMIC_FREE_KB=$((ARCHIVE_KB * 3 + 2 * 1024 * 1024))
if (( DYNAMIC_FREE_KB > MIN_FREE_KB )); then
  MIN_FREE_KB="$DYNAMIC_FREE_KB"
fi
if [[ -z "$FREE_KB" || "$FREE_KB" -lt "$MIN_FREE_KB" ]]; then
  echo "Insufficient free space: required ${MIN_FREE_KB} KiB, available ${FREE_KB:-unknown} KiB" >&2
  exit 1
fi
mkdir -p "$SOURCE_DIR" "$STAGE_DATA/media/legacy" "$STAGE_DATA/media-derivatives" "$REPORTS_DIR"
chmod 700 "$DEPLOY_ROOT" "$SOURCE_DIR" "$STAGE_DATA" "$REPORTS_DIR"
python3 - "$PROJECT_DIR/data" "$STAGE_DATA" <<'PY'
import os
import sys

if os.stat(sys.argv[1]).st_dev != os.stat(sys.argv[2]).st_dev:
    raise SystemExit("STATE_ROOT and production data/ must be on the same filesystem for atomic activation")
PY

(
  cd "$(dirname "$BUNDLE_ARCHIVE")"
  python3 - "$BUNDLE_ARCHIVE" "$BUNDLE_CHECKSUM" <<'PY'
import hashlib
import re
import sys
from pathlib import Path

archive = Path(sys.argv[1])
checksum = Path(sys.argv[2])
lines = checksum.read_text(encoding="ascii").splitlines()
if len(lines) != 1:
    raise SystemExit("Checksum file must contain exactly one SHA-256 record")
match = re.fullmatch(r"([0-9a-fA-F]{64})  ([^/\\]+)", lines[0])
if not match or match.group(2) != archive.name:
    raise SystemExit("Checksum record does not name the selected bundle archive")
digest = hashlib.sha256()
with archive.open("rb") as source:
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
if digest.hexdigest().lower() != match.group(1).lower():
    raise SystemExit("Bundle archive checksum mismatch")
PY
  sha256sum -c "$BUNDLE_CHECKSUM"
)
python3 - "$BUNDLE_ARCHIVE" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        normalized = member.name.removeprefix("./")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk() or member.isdev():
            raise SystemExit(f"Unsafe archive member: {member.name}")
PY
tar -C "$SOURCE_DIR" -xzf "$BUNDLE_ARCHIVE"
python3 -m scripts.validate_source_bundle verify "$SOURCE_DIR" >"$REPORTS_DIR/source-bundle-verification.json"
BUNDLE_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["git_sha"])' "$SOURCE_DIR/source-bundle-manifest.json")"
test "$BUNDLE_SHA" = "$CURRENT_SHA" || {
  echo "Bundle was created from $BUNDLE_SHA, deployment checkout is $CURRENT_SHA" >&2
  exit 1
}

sudo docker compose build "$SERVICE"
mapfile -t IMAGE_REFERENCES < <(sudo docker compose config --images)
test "${#IMAGE_REFERENCES[@]}" -eq 1 || { echo "Exactly one Compose image is required" >&2; exit 1; }
IMAGE_REFERENCE="${IMAGE_REFERENCES[0]}"
IMAGE_ID="$(sudo docker image inspect --format '{{.Id}}' "$IMAGE_REFERENCE")"
test -n "$IMAGE_ID"

rsync -a "$SOURCE_DIR/media/legacy/" "$STAGE_DATA/media/legacy/"
install -m 600 "$PROJECT_DIR/data/.gitkeep" "$STAGE_DATA/.gitkeep"
for artifact in legacy-crawl-checkpoint.json legacy-media-manifest.json full-import-plan.json current-sections.json structured-sections.json structured-import-dry-run.json known-public-routes.json source-bundle-manifest.json; do
  install -m 600 "$SOURCE_DIR/$artifact" "$STAGE_DATA/$artifact"
done
install -m 600 "$SOURCE_DIR/missing-legacy-media.csv" "$STAGE_DATA/missing-legacy-media.csv"
install -m 600 "$SOURCE_DIR/missing-legacy-media.md" "$STAGE_DATA/missing-legacy-media.md"

run_image() {
  sudo docker run --rm \
    --env-file "$ENV_FILE" \
    -v "$STAGE_DATA:/data" \
    "$IMAGE_REFERENCE" "$@"
}

run_image python -m server.importer \
  --source /data/structured-sections.json \
  --database /data/cms.sqlite3 \
  --media-manifest /data/legacy-media-manifest.json >"$REPORTS_DIR/structured-import.json"
run_image python -m server.full_import \
  --source /data/legacy-crawl-checkpoint.json \
  --database /data/cms.sqlite3 \
  --media-manifest /data/legacy-media-manifest.json \
  --plan /data/full-import-plan.json \
  --execute >"$REPORTS_DIR/full-import.json"
run_image python -m server.media_report \
  --plan /data/full-import-plan.json \
  --manifest /data/legacy-media-manifest.json \
  --csv /data/missing-legacy-media.csv \
  --markdown /data/missing-legacy-media.md >"$REPORTS_DIR/missing-media-report.json"
run_image python -m server.media_reconciliation \
  --database /data/cms.sqlite3 \
  --media /data/media \
  --manifest /data/legacy-media-manifest.json \
  --missing-report /data/missing-legacy-media.csv \
  --derivatives /data/media-derivatives \
  --apply >"$REPORTS_DIR/media-reconciliation.json"
run_image python -m server.search reindex --database /data/cms.sqlite3 >"$REPORTS_DIR/search-reindex.txt"
run_image python -m server.search verify --database /data/cms.sqlite3 >"$REPORTS_DIR/search-verify.txt"
run_image python -m server.migrations verify --database /data/cms.sqlite3 >"$REPORTS_DIR/migrations-verify.json"
python3 -m scripts.validate_source_bundle verify-database "$SOURCE_DIR" \
  --database "$STAGE_DATA/cms.sqlite3" >"$REPORTS_DIR/database-before-preparation.json"

run_image python -m server.migration_preparation preview \
  --database /data/cms.sqlite3 >"$REPORTS_DIR/preparation-preview.json"
PREVIEW_ID="$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1],encoding="utf-8")); assert data["status"]=="completed" and data["counts"].get("technical_blocker",0)==0; print(data["id"])' "$REPORTS_DIR/preparation-preview.json")"
run_image python -m server.migration_preparation apply \
  --database /data/cms.sqlite3 \
  --preview-run-id "$PREVIEW_ID" >"$REPORTS_DIR/preparation-apply.json"
python3 -c 'import json,sys; data=json.load(open(sys.argv[1],encoding="utf-8")); assert data["status"]=="completed" and data["counts"].get("technical_blocker",0)==0' "$REPORTS_DIR/preparation-apply.json"
run_image python -m server.migration_acceptance scan \
  --database /data/cms.sqlite3 --skip-external >"$REPORTS_DIR/post-apply-audit.json"
run_image python -m server.migration_preparation verify \
  --database /data/cms.sqlite3 >"$REPORTS_DIR/preparation-verify.json"
run_image python -m server.search verify --database /data/cms.sqlite3 >"$REPORTS_DIR/search-verify-after-preparation.txt"
run_image python -m server.migrations verify --database /data/cms.sqlite3 >"$REPORTS_DIR/migrations-verify-after-preparation.json"
python3 -m scripts.validate_source_bundle verify-database "$SOURCE_DIR" \
  --database "$STAGE_DATA/cms.sqlite3" >"$REPORTS_DIR/database-final.json"

ORIGINAL_MOVED=0
SUCCESS=0
rollback_activation() {
  local exit_code=$?
  set +e
  if [[ "$SUCCESS" -ne 1 && "$ORIGINAL_MOVED" -eq 1 ]]; then
    sudo docker compose rm -f -s "$SERVICE" >/dev/null 2>&1 || true
    if [[ -d "$PROJECT_DIR/data" && ! -e "$FAILED_DATA" ]]; then
      mv "$PROJECT_DIR/data" "$FAILED_DATA"
    fi
    if [[ -d "$ORIGINAL_DATA" ]]; then
      mv "$ORIGINAL_DATA" "$PROJECT_DIR/data"
    fi
    echo "Activation rolled back; failed data retained at $FAILED_DATA" >&2
  fi
  exit "$exit_code"
}
trap rollback_activation EXIT

mv "$PROJECT_DIR/data" "$ORIGINAL_DATA"
ORIGINAL_MOVED=1
mv "$STAGE_DATA" "$PROJECT_DIR/data"
sudo docker compose up -d "$SERVICE"
for _ in $(seq 1 90); do
  if curl -fsS "$HEALTH_URL" >"$REPORTS_DIR/health.json" 2>/dev/null; then
    break
  fi
  sleep 1
done
python3 -c 'import json,sys; data=json.load(open(sys.argv[1],encoding="utf-8")); assert data["status"]=="ok" and data["schema_version"]==sys.argv[2]' "$REPORTS_DIR/health.json" "$EXPECTED_CONTENT_SCHEMA"
curl -fsS "http://127.0.0.1:8000/cms.html" >/dev/null
curl -fsS "http://127.0.0.1:8000/api/public/content?limit=1" >/dev/null
sudo docker compose exec -T "$SERVICE" python -m server.migrations verify --database /data/cms.sqlite3 >/dev/null
sudo docker compose exec -T "$SERVICE" python -m server.search verify --database /data/cms.sqlite3 >/dev/null

if [[ "$VERIFY_EXTERNAL" == "1" ]]; then
  PUBLIC_BASE_URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["public_base_url"])' <(python3 scripts/validate_production_env.py "$ENV_FILE"))"
  curl -fsS "$PUBLIC_BASE_URL/" >/dev/null
  curl -fsS "$PUBLIC_BASE_URL/api/health" >/dev/null
fi

python3 - "$REPORTS_DIR/deployment.json" "$DEPLOY_ID" "$CURRENT_SHA" "$IMAGE_ID" "$BUNDLE_ARCHIVE" <<'PY'
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

output, deployment_id, git_sha, image_id, archive = sys.argv[1:]
hasher = hashlib.sha256()
with Path(archive).open("rb") as source:
    while chunk := source.read(1024 * 1024):
        hasher.update(chunk)
digest = hasher.hexdigest()
Path(output).write_text(json.dumps({
    "ok": True,
    "deployment_id": deployment_id,
    "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    "git_sha": git_sha,
    "image_id": image_id,
    "bundle_archive": archive,
    "bundle_sha256": digest,
}, indent=2) + "\n", encoding="utf-8")
PY
chmod 600 "$REPORTS_DIR"/*
SUCCESS=1
trap - EXIT
printf '%s\n' "$ATTEMPT_ID" >"$BUNDLE_STATE/latest-success"
chmod 600 "$BUNDLE_STATE/latest-success"

echo "CLEAN_BOOTSTRAP_APPLIED=true"
echo "DEPLOYMENT_ID=$DEPLOY_ID"
echo "DEPLOYMENT_ATTEMPT=$ATTEMPT_ID"
echo "IMPLEMENTATION_SHA=$CURRENT_SHA"
echo "IMAGE_ID=$IMAGE_ID"
echo "REPORTS_DIR=$REPORTS_DIR"
echo "ROLLBACK_SNAPSHOT=$ORIGINAL_DATA"
