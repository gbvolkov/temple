#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 5 keeps SQL schema 4 and changes only the content schema/editor/runtime.
# Reuse the proven restore-first deployment guard with stage-specific checks.
export DEPLOYMENT_STAGE=5
export EXPECTED_CONTENT_SCHEMA=1.2.0
export DEPLOYMENT_TAG=stage5-schema-driven-editor
export TEST_PORT="${TEST_PORT:-18005}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
