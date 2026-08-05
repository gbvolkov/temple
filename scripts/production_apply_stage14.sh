#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 14 adds the public service-information route for treby, preserves the
# imported draft for editorial confirmation, and repairs its legacy redirect.
export DEPLOYMENT_STAGE=14
export SOURCE_SCHEMA="${SOURCE_SCHEMA:-13}"
case "$SOURCE_SCHEMA" in
  13) ;;
  *) echo "Stage 14 supports only source schema 13" >&2; exit 1 ;;
esac
export EXPECTED_SCHEMA=14
export EXPECTED_CONTENT_SCHEMA=1.7.0
export DEPLOYMENT_TAG=stage14-treby-public-route
export TEST_PORT="${TEST_PORT:-18014}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
