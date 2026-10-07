#!/usr/bin/env bash
# Local DOGEAR issue server for the X4 / X4 Pro prototype. Never publishes.
#
#   tools/dev-server.sh build              # issues + web editions for this Mac's LAN address
#   tools/dev-server.sh serve [ISSUE_ID]   # point current.json at an issue and serve it
#   tools/dev-server.sh switch ISSUE_ID    # change current.json while the server runs
#   tools/dev-server.sh correct WEEK       # re-issue that week with new bytes (same id, new hash)
#
# Run from the repository root; proofs come from the canonical dataset (dataset/,
# the private dogear-editorial checkout). The firmware's DOGEAR_SERVER_BASE must be the same
# http://<LAN IP>:<PORT>, and the build must allow LAN HTTP
# (-DDOGEAR_ALLOW_LAN_HTTP); production fetches are verified HTTPS only.
set -euo pipefail
cd "$(dirname "$0")/.."

# This machine's LAN address: DOGEAR_HOST, else detected (macOS en0, then Linux).
HOST="${DOGEAR_HOST:-$( (ipconfig getifaddr en0 || hostname -I | awk '{print $1}') 2>/dev/null || true)}"
[ -n "$HOST" ] || { echo "set DOGEAR_HOST to this machine's LAN address" >&2; exit 2; }
PORT="${DOGEAR_PORT:-8080}"
WEEKS=(2026-10-12 2026-11-23 2026-11-30 2026-12-28)  # in order: each is history for the next

case "${1:-}" in
  build)
    rm -rf out-dev
    for w in "${WEEKS[@]}"; do
      python3 -m dogear.cli issue --week "$w" --out out-dev --issues out-dev --preview \
        --now "${w}T06:00:00+08:00" --base-url "http://${HOST}:${PORT}" --quiet
    done
    echo "built ${#WEEKS[@]} proof issues in out-dev/ for http://${HOST}:${PORT}"
    ;;
  serve)
    python3 -m dogear.cli stage --from out-dev --issue "${2:-dogear-2026-11-23}" --dir stage
    echo "serving stage/ on http://${HOST}:${PORT}  (Ctrl-C to stop)"
    exec python3 -m http.server "$PORT" --bind 0.0.0.0 --directory stage
    ;;
  switch)
    python3 -m dogear.cli stage --from out-dev --issue "${2:?issue id, e.g. dogear-2026-12-28}" --dir stage
    ;;
  correct)
    week="${2:?week Monday, e.g. 2026-11-23}"
    # A later generatedAt changes the bytes and so the hash, not the issue id:
    # the device must treat it as a correction of the same week.
    python3 -m dogear.cli issue --week "$week" --out out-dev --issues out-dev --preview \
      --now "${week}T07:30:00+08:00" --base-url "http://${HOST}:${PORT}" --quiet
    python3 -m dogear.cli stage --from out-dev --issue "dogear-${week}" --dir stage
    ;;
  *)
    sed -n '2,10p' "$0"
    exit 2
    ;;
esac
