#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 6 adds SQL migration 5 and indexes the existing immutable media archive.
# The shared deployment guard first migrates and indexes a restored backup, then
# repeats the same operation on production only after every restore check passes.
export DEPLOYMENT_STAGE=6
export SOURCE_SCHEMA=4
export EXPECTED_SCHEMA=5
export EXPECTED_CONTENT_SCHEMA=1.3.0
export DEPLOYMENT_TAG=stage6-full-media-library
export TEST_PORT="${TEST_PORT:-18006}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
