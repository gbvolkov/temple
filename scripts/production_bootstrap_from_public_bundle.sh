#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/temple}"
DEPLOY_ROOT="${DEPLOY_ROOT:-$HOME/temple-clean-deployments}"
ARCHIVE="${1:-}"
CHECKSUM="${2:-${ARCHIVE:+$ARCHIVE.sha256}}"
SERVICE="${SERVICE:-cms}"
VERIFY_EXTERNAL="${VERIFY_EXTERNAL:-0}"
EXPECTED_SCHEMA=14
EXPECTED_CONTENT_SCHEMA=1.7.0

if [[ -z "$ARCHIVE" || -z "$CHECKSUM" ]]; then
  echo "Usage: $0 public-source-YYYYMMDDTHHMMSSZ.tar.gz [archive.sha256]" >&2
  exit 1
fi
for command_name in find git curl docker python3 rsync seq sudo tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done

cd "$PROJECT_DIR"
test -r "$ARCHIVE"
test -r "$CHECKSUM"
test -r .env
test -r docker-compose.yml
test -r Dockerfile

if [[ "$(git branch --show-current)" != "main" ]]; then
  echo "Clean production bootstrap is allowed only from main" >&2
  exit 1
fi
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  echo "Local main must exactly match origin/main" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "The production checkout is not clean" >&2
  exit 1
fi
if [[ -n "$(find data -mindepth 1 ! -name .gitkeep -print -quit 2>/dev/null)" ]]; then
  echo "data/ already contains runtime files; use an upgrade/restore workflow instead" >&2
  exit 1
fi
if [[ -n "$(sudo docker compose ps -q "$SERVICE")" ]]; then
  echo "CMS container already exists; clean bootstrap refuses an active installation" >&2
  exit 1
fi

python3 - .env <<'PY'
import sys
from pathlib import Path

values = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")

required = ("CMS_ENV", "CMS_BOOTSTRAP_USER", "CMS_BOOTSTRAP_PASSWORD",
            "PUBLIC_BASE_URL", "SUBMISSION_IP_HASH_SECRET",
            "SUBMISSION_TRUSTED_PROXY_NETWORKS")
missing = [name for name in required if not values.get(name)]
if missing:
    raise SystemExit("Missing production environment values: " + ", ".join(missing))
if values["CMS_ENV"] != "production":
    raise SystemExit("CMS_ENV must be production")
if len(values["CMS_BOOTSTRAP_PASSWORD"]) < 20:
    raise SystemExit("CMS_BOOTSTRAP_PASSWORD must contain at least 20 characters")
if len(values["SUBMISSION_IP_HASH_SECRET"]) < 32:
    raise SystemExit("SUBMISSION_IP_HASH_SECRET must contain at least 32 characters")
if any("replace-with" in values[name].lower() for name in required):
    raise SystemExit("Placeholder values are forbidden in production .env")
smtp = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
        "SMTP_SECURITY", "SUBMISSION_NOTIFY_TO")
configured = [bool(values.get(name)) for name in smtp]
if any(configured) and not all(configured):
    raise SystemExit("SMTP must be fully configured or fully disabled")
PY

mkdir -p "$DEPLOY_ROOT"
chmod 700 "$DEPLOY_ROOT"
ARCHIVE_REPORT="$(python3 -m server.migration_bundle verify-archive \
  --archive "$ARCHIVE" --checksum "$CHECKSUM")"
BUNDLE_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["root"])' <<<"$ARCHIVE_REPORT")"
case "$BUNDLE_ID" in
  public-source-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Unsafe bundle root: $BUNDLE_ID" >&2; exit 1 ;;
esac

DEPLOYMENT_ID="clean-${BUNDLE_ID#public-source-}"
STAGE="$DEPLOY_ROOT/staging-$DEPLOYMENT_ID"
REPORT_ROOT="$DEPLOY_ROOT/reports-$DEPLOYMENT_ID"
if [[ -e "$STAGE" || -e "$REPORT_ROOT" ]]; then
  echo "Deployment workspace already exists: $DEPLOYMENT_ID" >&2
  exit 1
fi
mkdir -p "$STAGE/bundle" "$STAGE/data" "$REPORT_ROOT"
chmod 700 "$STAGE" "$REPORT_ROOT"
tar -C "$STAGE/bundle" -xzf "$ARCHIVE"
BUNDLE="$STAGE/bundle/$BUNDLE_ID"
python3 -m server.migration_bundle verify --root "$BUNDLE" \
  >"$REPORT_ROOT/bundle-verification.json"

BUNDLE_CODE_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["code_sha"])' "$BUNDLE/bundle-manifest.json")"
CURRENT_SHA="$(git rev-parse HEAD)"
if [[ "$BUNDLE_CODE_SHA" != "$CURRENT_SHA" ]]; then
  echo "Bundle code SHA $BUNDLE_CODE_SHA differs from deployment SHA $CURRENT_SHA" >&2
  exit 1
fi

cp "$BUNDLE/source/legacy-crawl-checkpoint.json" "$STAGE/data/"
cp "$BUNDLE/source/legacy-media-manifest.json" "$STAGE/data/"
cp "$BUNDLE/source/full-import-plan.json" "$STAGE/data/"
cp "$BUNDLE/source/missing-legacy-media.csv" "$STAGE/data/"
cp "$BUNDLE/source/missing-legacy-media.md" "$STAGE/data/"
cp "$BUNDLE/bundle-manifest.json" "$STAGE/data/migration-bundle-manifest.json"
mkdir -p "$STAGE/data/media/legacy" "$STAGE/data/media-derivatives"
rsync -a "$BUNDLE/media/legacy/" "$STAGE/data/media/legacy/"

sudo docker compose build "$SERVICE"
mapfile -t IMAGE_REFERENCES < <(sudo docker compose config --images)
if [[ "${#IMAGE_REFERENCES[@]}" -ne 1 ]]; then
  echo "Expected exactly one Compose image" >&2
  exit 1
fi
IMAGE_REFERENCE="${IMAGE_REFERENCES[0]}"
IMAGE_ID="$(sudo docker image inspect --format '{{.Id}}' "$IMAGE_REFERENCE")"
test -n "$IMAGE_ID"

run_cms() {
  sudo docker run --rm \
    --env-file .env \
    -v "$STAGE/data:/data" \
    "$IMAGE_REFERENCE" "$@"
}

run_cms python -m server.full_import \
  --source /data/legacy-crawl-checkpoint.json \
  --database /data/cms.sqlite3 \
  --media-manifest /data/legacy-media-manifest.json \
  --plan /data/full-import-plan.json \
  --execute >"$REPORT_ROOT/import.json"

run_cms python -m server.media_report \
  --plan /data/full-import-plan.json \
  --manifest /data/legacy-media-manifest.json \
  --csv /data/missing-legacy-media.csv \
  --markdown /data/missing-legacy-media.md >"$REPORT_ROOT/missing-media.json"

run_cms python -m server.media_reconciliation \
  --database /data/cms.sqlite3 \
  --media /data/media \
  --manifest /data/legacy-media-manifest.json \
  --missing-report /data/missing-legacy-media.csv \
  --derivatives /data/media-derivatives \
  --apply >"$REPORT_ROOT/media-reconciliation.json"

python3 - "$REPORT_ROOT/media-reconciliation.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if value["reconciliation"]["status"] == "ok" else 1)
PY

run_cms python -m server.search reindex --database /data/cms.sqlite3 \
  >"$REPORT_ROOT/search-reindex.txt"
run_cms python -m server.search verify --database /data/cms.sqlite3 \
  >"$REPORT_ROOT/search-verify.txt"
run_cms python -m server.migrations verify --database /data/cms.sqlite3 \
  >"$REPORT_ROOT/schema-verification.json"

run_cms python -m server.migration_acceptance scan --database /data/cms.sqlite3 \
  >"$REPORT_ROOT/preparation-audit.json"
run_cms python -m server.migration_preparation preview \
  --database /data/cms.sqlite3 --skip-audit \
  >"$REPORT_ROOT/preparation-preview.json"
PREVIEW_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["id"])' "$REPORT_ROOT/preparation-preview.json")"
test -n "$PREVIEW_ID"
run_cms python -m server.migration_preparation apply \
  --database /data/cms.sqlite3 --preview-run-id "$PREVIEW_ID" \
  >"$REPORT_ROOT/preparation-apply.json"
run_cms python -m server.migration_acceptance scan --database /data/cms.sqlite3 \
  >"$REPORT_ROOT/post-apply-audit.json"
run_cms python -m server.migration_preparation verify --database /data/cms.sqlite3 \
  >"$REPORT_ROOT/preparation-verify.json"

python3 -m server.migration_bundle verify-database \
  --database "$STAGE/data/cms.sqlite3" \
  --bundle-root "$BUNDLE" >"$REPORT_ROOT/database-verification.json"

run_cms python -m server.media_reconciliation \
  --database /data/cms.sqlite3 \
  --media /data/media \
  --manifest /data/legacy-media-manifest.json \
  --missing-report /data/missing-legacy-media.csv \
  --derivatives /data/media-derivatives \
  >"$REPORT_ROOT/final-media-reconciliation.json"
python3 - "$REPORT_ROOT/final-media-reconciliation.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if value["reconciliation"]["status"] == "ok" else 1)
PY

rsync -a "$STAGE/data/" data/
sudo docker compose up -d "$SERVICE"
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8000/api/health >"$REPORT_ROOT/health.json" 2>/dev/null; then
    break
  fi
  sleep 1
done
python3 - "$REPORT_ROOT/health.json" "$EXPECTED_CONTENT_SCHEMA" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["status"] == "ok"
assert value["schema_version"] == sys.argv[2]
PY
curl -fsS http://127.0.0.1:8000/ >/dev/null
curl -fsS http://127.0.0.1:8000/api/public/content?limit=1 >/dev/null

python3 -m server.migration_bundle verify-database \
  --database data/cms.sqlite3 \
  --bundle-root "$BUNDLE" >"$REPORT_ROOT/activated-database-verification.json"

if [[ "$VERIFY_EXTERNAL" == "1" ]]; then
  PUBLIC_BASE_URL="$(python3 - .env <<'PY'
import sys
from pathlib import Path
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if raw.strip().startswith("PUBLIC_BASE_URL="):
        print(raw.split("=", 1)[1].strip().strip('"').strip("'").rstrip("/"))
        break
PY
)"
  test -n "$PUBLIC_BASE_URL"
  curl -fsS "$PUBLIC_BASE_URL/" >/dev/null
  curl -fsS "$PUBLIC_BASE_URL/api/health" >/dev/null
fi

python3 - "$REPORT_ROOT/deployment.json" "$DEPLOYMENT_ID" "$CURRENT_SHA" "$IMAGE_ID" "$BUNDLE_ID" <<'PY'
import json, sys
from datetime import UTC, datetime
path, deployment_id, code_sha, image_id, bundle_id = sys.argv[1:]
with open(path, "w", encoding="utf-8") as output:
    json.dump({
        "ok": True,
        "deployment_id": deployment_id,
        "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code_sha": code_sha,
        "image_id": image_id,
        "bundle_id": bundle_id,
        "schema_version": 14,
    }, output, ensure_ascii=False, indent=2)
PY
chmod 600 "$REPORT_ROOT"/*

echo "CLEAN_BOOTSTRAP_APPLIED=true"
echo "DEPLOYMENT_ID=$DEPLOYMENT_ID"
echo "BUNDLE_ID=$BUNDLE_ID"
echo "IMPLEMENTATION_SHA=$CURRENT_SHA"
echo "IMAGE_ID=$IMAGE_ID"
echo "REPORT_ROOT=$REPORT_ROOT"
