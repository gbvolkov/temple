#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 8 adds SQL migration 7, public visitor forms, the protected CMS queue,
# notification outbox and retention. The shared guard first exercises the flow
# on a restored copy with real email delivery explicitly disabled.
export DEPLOYMENT_STAGE=8
export SOURCE_SCHEMA=6
export EXPECTED_SCHEMA=7
export EXPECTED_CONTENT_SCHEMA=1.4.0
export DEPLOYMENT_TAG=stage8-visitor-submissions
export TEST_PORT="${TEST_PORT:-18008}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
