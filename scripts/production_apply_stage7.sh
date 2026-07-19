#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 7 adds SQL migration 6, user/session administration and atomic bulk workflow.
# The shared deployment guard exercises all new roles on a disposable restored
# database before it is allowed to migrate the production database.
export DEPLOYMENT_STAGE=7
export SOURCE_SCHEMA=5
export EXPECTED_SCHEMA=6
export EXPECTED_CONTENT_SCHEMA=1.4.0
export DEPLOYMENT_TAG=stage7-users-and-editorial-workflow
export TEST_PORT="${TEST_PORT:-18007}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
