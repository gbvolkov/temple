#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 10 adds SQL migration 9 and the migration editorial-acceptance
# workflow. The shared guard audits a restored copy before production is
# migrated. Production rollout creates only a draft pilot batch.
export DEPLOYMENT_STAGE=10
export SOURCE_SCHEMA=8
export EXPECTED_SCHEMA=9
export EXPECTED_CONTENT_SCHEMA=1.6.0
export DEPLOYMENT_TAG=stage10-migration-editorial-acceptance
export TEST_PORT="${TEST_PORT:-18010}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
