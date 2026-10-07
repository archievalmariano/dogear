#!/usr/bin/env bash
# Deploy-key isolation check for ONE GitHub Environment (.github/workflows/dogear-key-check.yml).
#
#   ENVIRONMENT=editorial-gate|production|staging OWNER=<owner> \
#   EDITORIAL_KEY=<secret or empty> DATA_KEY=<secret or empty> tools/key_check.sh
#
# Each key the Environment holds is tried against all three private repositories:
#   read   a shallow clone with no checkout (then deleted)
#   write  `git push --dry-run` of a throwaway commit to a new branch name: GitHub
#          authorises the write before the dry run stops, so a read-only or foreign
#          key is refused, and nothing is ever sent
# and the access found must equal the design exactly:
#   editorial key  dogear-editorial read; nothing else          (editorial-gate, production)
#   data key       its own publication-data repository write;   (production, staging)
#                  nothing else
#   staging        holds no editorial key; editorial-gate holds no data key
# Prints only that matrix: never key material, repository content or raw errors.
set -euo pipefail
umask 077

EDITORIAL=dogear-editorial
PROD_DATA=dogear-publication-data
STAGING_DATA=dogear-publication-data-staging
REPOS="$EDITORIAL $PROD_DATA $STAGING_DATA"

case "${ENVIRONMENT:-}" in
  editorial-gate) want_editorial=yes; data_repo="" ;;
  production)     want_editorial=yes; data_repo=$PROD_DATA ;;
  staging)        want_editorial=no;  data_repo=$STAGING_DATA ;;
  *) echo "key check: ENVIRONMENT must be editorial-gate, production or staging"; exit 2 ;;
esac
[ -n "${OWNER:-}" ] || { echo "key check: OWNER is required"; exit 2; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# GitHub's published SSH host keys (api.github.com/meta, 2026-10-08).
cat > "$work/known_hosts" <<'HOSTS'
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=
HOSTS

# A throwaway local commit for the dry-run write probes; it never leaves the runner.
git init -q "$work/probe"
git -C "$work/probe" -c user.name=key-check -c user.email=key-check@invalid commit -q --allow-empty -m probe

# Only GitHub's own refusals count as "denied": a key with no access to the
# repository, an unknown key, or a read-only key asked to write. Anything else (a
# network failure, a host-key mismatch, a malformed key) is an error, never "none".
denied() {
  grep -qE "ERROR: Repository not found|Permission denied \(publickey\)|key you are authenticating with has been marked as read only" "$1"
}

# access KEYFILE REPO -> none | read | write | error
access() {
  local ssh="ssh -F /dev/null -i $1 -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes"
  ssh="$ssh -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$work/known_hosts"
  local url="git@github.com:$OWNER/$2.git" read=none write=none
  rm -rf "$work/clone"
  if GIT_SSH_COMMAND="$ssh" git clone -q --depth 1 --no-checkout "$url" "$work/clone" 2> "$work/err"; then
    read=yes
  elif ! denied "$work/err"; then
    echo error; return
  fi
  rm -rf "$work/clone"
  if GIT_SSH_COMMAND="$ssh" git -C "$work/probe" push -q --dry-run "$url" HEAD:refs/heads/dogear-key-check-probe 2> "$work/err"; then
    write=yes
  elif ! denied "$work/err"; then
    echo error; return
  fi
  if [ "$write" = yes ] && [ "$read" = yes ]; then echo write
  elif [ "$write" = yes ]; then echo error  # writable but unreadable: not a deploy-key shape
  elif [ "$read" = yes ]; then echo read
  else echo none
  fi
}

failures=0
report() {  # LABEL FOUND WANTED
  if [ "$2" = "$3" ]; then echo "ok    $1: $2"; else echo "FAIL  $1: $2 (expected $3)"; failures=$((failures + 1)); fi
}

check_key() {  # NAME VALUE OWN_REPO OWN_ACCESS
  local name=$1 value=$2 own=$3 own_access=$4 repo want
  printf '%s\n' "$value" > "$work/$name"
  for repo in $REPOS; do
    want=none
    [ "$repo" = "$own" ] && want=$own_access
    report "$ENVIRONMENT $name -> $repo" "$(access "$work/$name" "$repo")" "$want"
  done
  rm -f "$work/$name"
}

echo "deploy-key check for the $ENVIRONMENT environment (no writes: dry runs only)"
if [ "$want_editorial" = yes ]; then
  if [ -n "${EDITORIAL_KEY:-}" ]; then check_key EDITORIAL_DEPLOY_KEY "$EDITORIAL_KEY" "$EDITORIAL" read
  else report "$ENVIRONMENT EDITORIAL_DEPLOY_KEY" absent present; fi
else
  report "$ENVIRONMENT EDITORIAL_DEPLOY_KEY" "$([ -n "${EDITORIAL_KEY:-}" ] && echo present || echo absent)" absent
fi
if [ -n "$data_repo" ]; then
  if [ -n "${DATA_KEY:-}" ]; then check_key PUBLICATION_DATA_DEPLOY_KEY "$DATA_KEY" "$data_repo" write
  else report "$ENVIRONMENT PUBLICATION_DATA_DEPLOY_KEY" absent present; fi
else
  report "$ENVIRONMENT PUBLICATION_DATA_DEPLOY_KEY" "$([ -n "${DATA_KEY:-}" ] && echo present || echo absent)" absent
fi

if [ "$failures" -ne 0 ]; then echo "key check FAILED: $failures"; exit 1; fi
echo "key check passed"
