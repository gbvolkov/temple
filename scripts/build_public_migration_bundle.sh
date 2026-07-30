#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)}"
SOURCE_BASE_URL="${SOURCE_BASE_URL:-https://www.sv-innokenty.ru/}"
BUNDLE_ROOT="${BUNDLE_ROOT:-$PROJECT_DIR/work/migration-bundles}"
BUNDLE_ID="${1:-${BUNDLE_ID:-public-source-$(date -u +%Y%m%dT%H%M%SZ)}}"
CRAWL_DELAY="${CRAWL_DELAY:-0.25}"
MEDIA_DELAY="${MEDIA_DELAY:-0.15}"
MEDIA_WORKERS="${MEDIA_WORKERS:-4}"
MEDIA_MAX_MB="${MEDIA_MAX_MB:-50}"
INSTALL_BROWSER="${INSTALL_BROWSER:-0}"

case "$BUNDLE_ID" in
  public-source-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Unsafe BUNDLE_ID: $BUNDLE_ID" >&2; exit 1 ;;
esac

for command_name in git uv tar sha256sum date; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done

cd "$PROJECT_DIR"
test -r current-sections.json
test -r site/cms-schema.json
if ! git diff --quiet -- . ':(exclude)data/**' ':(exclude)work/**'; then
  echo "Tracked code changes are present; build a source bundle from a committed checkout" >&2
  exit 1
fi

case "$SOURCE_BASE_URL" in
  http://*|https://*) ;;
  *) echo "SOURCE_BASE_URL must be an absolute HTTP(S) URL" >&2; exit 1 ;;
esac

mkdir -p "$BUNDLE_ROOT"
chmod 700 "$BUNDLE_ROOT"
BUNDLE_DIR="$BUNDLE_ROOT/$BUNDLE_ID"
ARCHIVE="$BUNDLE_ROOT/$BUNDLE_ID.tar.gz"
CHECKSUM="$ARCHIVE.sha256"
if [[ -e "$BUNDLE_DIR" || -e "$ARCHIVE" || -e "$CHECKSUM" ]]; then
  echo "Bundle destination already exists: $BUNDLE_ID" >&2
  exit 1
fi

mkdir -p "$BUNDLE_DIR/source" "$BUNDLE_DIR/media/legacy"
chmod 700 "$BUNDLE_DIR"
cp current-sections.json "$BUNDLE_DIR/source/current-sections.json"

uv sync --group crawl
if [[ "$INSTALL_BROWSER" == "1" ]]; then
  uv run playwright install chromium
fi

uv run python -m server.legacy_crawl \
  --base-url "$SOURCE_BASE_URL" \
  --output "$BUNDLE_DIR/source/legacy-crawl-checkpoint.json" \
  --delay "$CRAWL_DELAY" \
  --fresh

uv run python -m server.legacy_inventory \
  --crawl "$BUNDLE_DIR/source/legacy-crawl-checkpoint.json" \
  --sections "$BUNDLE_DIR/source/current-sections.json" \
  --json "$BUNDLE_DIR/source/legacy-inventory.json" \
  --markdown "$BUNDLE_DIR/source/legacy-inventory.md"

uv run python -m server.media_mirror \
  --source "$BUNDLE_DIR/source/legacy-crawl-checkpoint.json" \
  --destination "$BUNDLE_DIR/media/legacy" \
  --manifest "$BUNDLE_DIR/source/legacy-media-manifest.json" \
  --max-mb "$MEDIA_MAX_MB" \
  --delay "$MEDIA_DELAY" \
  --workers "$MEDIA_WORKERS"

uv run python -m server.media_mirror \
  --source "$BUNDLE_DIR/source/legacy-crawl-checkpoint.json" \
  --destination "$BUNDLE_DIR/media/legacy" \
  --manifest "$BUNDLE_DIR/source/legacy-media-manifest.json" \
  --max-mb "$MEDIA_MAX_MB" \
  --delay "$MEDIA_DELAY" \
  --workers "$MEDIA_WORKERS" \
  --execute

uv run python -m server.full_import \
  --source "$BUNDLE_DIR/source/legacy-crawl-checkpoint.json" \
  --media-manifest "$BUNDLE_DIR/source/legacy-media-manifest.json" \
  --plan "$BUNDLE_DIR/source/full-import-plan.json"

uv run python -m server.media_report \
  --plan "$BUNDLE_DIR/source/full-import-plan.json" \
  --manifest "$BUNDLE_DIR/source/legacy-media-manifest.json" \
  --csv "$BUNDLE_DIR/source/missing-legacy-media.csv" \
  --markdown "$BUNDLE_DIR/source/missing-legacy-media.md"

CODE_SHA="$(git rev-parse HEAD)"
uv run python -m server.migration_bundle create \
  --root "$BUNDLE_DIR" \
  --source-url "$SOURCE_BASE_URL" \
  --code-sha "$CODE_SHA"
uv run python -m server.migration_bundle verify --root "$BUNDLE_DIR"

tar -C "$BUNDLE_ROOT" -czf "$ARCHIVE" "$BUNDLE_ID"
sha256sum "$ARCHIVE" >"$CHECKSUM"
chmod 600 "$ARCHIVE" "$CHECKSUM"
uv run python -m server.migration_bundle verify-archive \
  --archive "$ARCHIVE" \
  --checksum "$CHECKSUM"

echo "BUNDLE_ID=$BUNDLE_ID"
echo "ARCHIVE=$ARCHIVE"
echo "CHECKSUM=$CHECKSUM"
echo "SOURCE_BASE_URL=$SOURCE_BASE_URL"
echo "CODE_SHA=$CODE_SHA"
