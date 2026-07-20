#!/usr/bin/env bash
set -Eeuo pipefail

# Stage 9 adds SQL migration 8, public FTS5 search, complete public SEO,
# social previews, sitemap, robots and RSS. The shared guard migrates and
# exercises a restored copy before the production database is touched.
export DEPLOYMENT_STAGE=9
export SOURCE_SCHEMA=7
export EXPECTED_SCHEMA=8
export EXPECTED_CONTENT_SCHEMA=1.5.0
export DEPLOYMENT_TAG=stage9-search-and-seo
export TEST_PORT="${TEST_PORT:-18009}"
export DEPLOYMENT_ENTRYPOINT="$0"

exec "$BASH" "${BASH_SOURCE[0]%/*}/production_apply_stage4.sh" "$@"
