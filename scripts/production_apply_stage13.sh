#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 13 hardens preparation identities, semantic verification, recovery,
# external evidence and generated-media provenance.  Production mutation is
# allowed only after the same full workflow succeeds on a restored copy.
export DEPLOYMENT_STAGE=13
export SOURCE_SCHEMA="${SOURCE_SCHEMA:-11}"
case "$SOURCE_SCHEMA" in
  11|12) ;;
  *) echo "Stage 13 supports only source schema 11 or 12" >&2; exit 1 ;;
esac
export EXPECTED_SCHEMA=13
export EXPECTED_CONTENT_SCHEMA=1.6.0
export DEPLOYMENT_TAG=stage13-migration-preparation-hardening
export TEST_PORT="${TEST_PORT:-18013}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
