#!/usr/bin/env bash
set -Eeuo pipefail

# Upgrade an existing schema 14 installation using the established restore and
# verification gate. Schema 15 adds assessment and acceptance journals.
export DEPLOYMENT_STAGE=15
export SOURCE_SCHEMA="${SOURCE_SCHEMA:-14}"
case "$SOURCE_SCHEMA" in
  14|15) ;;
  *) echo "Stage 15 supports source schema 14 or idempotent recovery from 15" >&2; exit 1 ;;
esac
export EXPECTED_SCHEMA=15
export EXPECTED_CONTENT_SCHEMA=1.7.0
export DEPLOYMENT_TAG=stage15-editorial-acceptance
export TEST_PORT="${TEST_PORT:-18015}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
