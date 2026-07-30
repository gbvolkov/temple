#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)}"
ARCHIVE="${1:-}"
CHECKSUM="${2:-${ARCHIVE:+$ARCHIVE.sha256}}"
VERIFY_ROOT="${VERIFY_ROOT:-$PROJECT_DIR/work/migration-bundle-verification}"

if [[ -z "$ARCHIVE" || -z "$CHECKSUM" ]]; then
  echo "Usage: $0 public-source-YYYYMMDDTHHMMSSZ.tar.gz [archive.sha256]" >&2
  exit 1
fi
for command_name in uv tar python3; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Required command is missing: $command_name" >&2
    exit 1
  }
done
test -r "$ARCHIVE"
test -r "$CHECKSUM"

cd "$PROJECT_DIR"
ARCHIVE_REPORT="$(uv run python -m server.migration_bundle verify-archive \
  --archive "$ARCHIVE" --checksum "$CHECKSUM")"
BUNDLE_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["root"])' <<<"$ARCHIVE_REPORT")"
case "$BUNDLE_ID" in
  public-source-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
  *) echo "Unsafe bundle root: $BUNDLE_ID" >&2; exit 1 ;;
esac

DESTINATION="$VERIFY_ROOT/$BUNDLE_ID"
if [[ -e "$DESTINATION" ]]; then
  echo "Verification destination already exists: $DESTINATION" >&2
  exit 1
fi
mkdir -p "$VERIFY_ROOT"
chmod 700 "$VERIFY_ROOT"
tar -C "$VERIFY_ROOT" -xzf "$ARCHIVE"
uv run python -m server.migration_bundle verify --root "$DESTINATION"

echo "BUNDLE_ID=$BUNDLE_ID"
echo "VERIFIED_ROOT=$DESTINATION"
