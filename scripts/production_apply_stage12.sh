#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 12 previews and applies deterministic migration preparation on a
# disposable restored copy before repeating the verified operation in production.
export DEPLOYMENT_STAGE=12
export SOURCE_SCHEMA=11
export EXPECTED_SCHEMA=12
export EXPECTED_CONTENT_SCHEMA=1.6.0
export DEPLOYMENT_TAG=stage12-automatic-migration-preparation
export TEST_PORT="${TEST_PORT:-18012}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
