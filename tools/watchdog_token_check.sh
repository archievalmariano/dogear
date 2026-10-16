#!/usr/bin/env bash
# Read-only check of the watchdog's GitHub token (DOGEAR-WATCHDOG-PLAN §4), run by the
# owner before the staging rehearsal counts. It sends exactly the watchdog's five GET
# requests with the token and prints only each path and its HTTP status code: never a
# response body, never the token. It never follows a redirect, so the token is never
# sent beyond api.github.com.
#
#   GITHUB_READ_TOKEN=... tools/watchdog_token_check.sh [RUN_ID]
#
# RUN_ID: any run of the repository, for the jobs and logs checks (default: the newest
# DOGEAR promote run). Expected: 200, 200, 200, 302, 200. Exit 0 only if all match.
set -euo pipefail

: "${GITHUB_READ_TOKEN:?set GITHUB_READ_TOKEN (the watchdog read-only token) in the environment}"
OWNER="archievalmariano"
REPO="dogear"
API="https://api.github.com/repos/$OWNER/$REPO"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
chmod 700 "$tmp"
# The header reaches curl from a file, so the token is never on a command line.
printf 'Authorization: Bearer %s\n' "$GITHUB_READ_TOKEN" > "$tmp/auth"

status() { # PATH BODYFILE: prints the HTTP status code (000 if curl failed)
  curl -sS --proto '=https' --max-redirs 0 -o "$2" -w '%{http_code}' \
    -H @"$tmp/auth" -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
    -H 'User-Agent: dogear-watchdog-token-check' "$API/$1" 2>/dev/null || printf '000'
}

first_id() { # BODYFILE runs|jobs: a numeric id, without printing the body
  # runs: the newest run. jobs: the first job that ran and so has a log (completed, not
  # skipped, with steps); a skipped or never-started job has none.
  python3 -I - "$1" "$2" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    if sys.argv[2] == "runs":
        item = d["workflow_runs"][0]
    else:
        item = next(j for j in d["jobs"] if j.get("status") == "completed"
                    and j.get("conclusion") not in (None, "skipped") and j.get("steps"))
    print(int(item["id"]))
except Exception:
    pass
PY
}

fail=0
check() { # LABEL CODE EXPECTED
  printf '%-58s %s\n' "$1" "$2"
  if [ "$2" != "$3" ]; then fail=1; fi
}

check "GET actions/workflows/dogear-stage.yml" "$(status 'actions/workflows/dogear-stage.yml' "$tmp/1")" 200
check "GET actions/workflows/dogear-promote.yml/runs" "$(status 'actions/workflows/dogear-promote.yml/runs?per_page=1' "$tmp/2")" 200

run="${1:-}"
if [ -z "$run" ]; then run="$(first_id "$tmp/2" runs)"; fi
if ! printf '%s' "$run" | grep -Eq '^[0-9]+$'; then
  echo "no run to check jobs and logs with: pass any run id of $OWNER/$REPO as the first argument" >&2
  exit 2
fi
check "GET actions/runs/$run/jobs" "$(status "actions/runs/$run/jobs?per_page=50" "$tmp/3")" 200

job="$(first_id "$tmp/3" jobs)"
if ! printf '%s' "$job" | grep -Eq '^[0-9]+$'; then
  echo "run $run has no job that ran (completed, not skipped) to check the logs permission with" >&2
  exit 2
fi
check "GET actions/jobs/$job/logs, redirect not followed" "$(status "actions/jobs/$job/logs" "$tmp/4")" 302
check "GET commits?sha=main&per_page=1" "$(status 'commits?sha=main&per_page=1' "$tmp/5")" 200

if [ "$fail" -ne 0 ]; then
  echo "token check: FAIL (401, 403 or 404 means a missing permission: Actions, Contents and Metadata read)" >&2
  exit 1
fi
echo "token check: PASS"
