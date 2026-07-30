#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 11 adds durable media-reindex summaries and performs deterministic
# media reconciliation on a restored copy before applying it to production.
export DEPLOYMENT_STAGE=11
export SOURCE_SCHEMA=9
export EXPECTED_SCHEMA=11
export EXPECTED_CONTENT_SCHEMA=1.6.0
export DEPLOYMENT_TAG=stage11-media-reconciliation
export TEST_PORT="${TEST_PORT:-18011}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
