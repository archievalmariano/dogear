#!/usr/bin/env bash
# Builds the static site for staging S1 (docs/PUBLISHING.md §10) into
# site-staging/. Synthetic content only: fixtures/staging-synthetic.json, never
# the canonical dataset. Uploading it is a separate, approved step.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="https://dogear-staging.pages.dev"
WEEK="2027-02-01"  # a week neither test device has cached
rm -rf out-staging site-staging
python3 -m dogear.cli --dataset fixtures/staging-synthetic.json issue --week "$WEEK" \
  --out out-staging --issues out-staging --preview --now "2026-10-07T12:00:00+08:00" \
  --base-url "$BASE" --quiet
python3 -m dogear.cli stage --from out-staging --issue "dogear-$WEEK" --dir site-staging
# Pages applies _headers to static files. noindex is labelling, not access
# control; robots.txt stays permissive so crawlers can see it.
cat > site-staging/_headers <<'HEADERS'
/*
  X-Robots-Tag: noindex, nofollow
/current.json
  Cache-Control: no-cache, must-revalidate
HEADERS
echo "site-staging/ ready for $BASE"
( cd site-staging && find . -type f ! -name '.*' | sort | while read -r f; do shasum -a 256 "$f"; done )
