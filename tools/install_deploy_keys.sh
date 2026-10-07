#!/usr/bin/env bash
# Install DOGEAR's three deploy keys and their GitHub Environment secrets.
# Run by the repository OWNER in their own terminal (macOS), never by CI or an agent.
#
#   key                          repository (deploy key)            access      Environment secret(s)
#   EDITORIAL_DEPLOY_KEY         dogear-editorial                   read-only   editorial-gate, production
#   PUBLICATION_DATA_DEPLOY_KEY  dogear-publication-data            write       production
#   PUBLICATION_DATA_DEPLOY_KEY  dogear-publication-data-staging    write       staging
#
# A separate ed25519 keypair per repository. For each one the script:
#   1. generates it in a private temporary directory (mode 700);
#   2. copies the PUBLIC key to the clipboard and opens that repository's
#      "Add deploy key" page, where you paste it and set "Allow write access".
#      (Through the web page, not `gh repo deploy-key add`: keys added by gh are
#      tied to gh's login token and vanish if that token is ever revoked.);
#   3. waits until GitHub lists exactly that public key with the right access;
#   4. stores the PRIVATE key as the Environment secret(s) with `gh secret set`,
#      which reads it from the file (never an argument, never printed);
#   5. deletes the private key. Only GitHub keeps it, as encrypted secrets.
# Nothing here is printed except titles, access flags and public fingerprints.
# It refuses to replace anything that already exists: to rotate a key, delete its
# deploy key and Environment secret(s) on GitHub first, then run it again.
#
# Private keys exist only in "$TMPDIR"/dogear-deploy-keys.XXXXXX (mode 700), and
# only until each is stored. Any exit (success, a failure, q, Ctrl-C, SIGTERM,
# SIGHUP when the terminal closes) deletes that folder. Nothing can clean up
# after SIGKILL (kill -9), a crash or a power loss: the next run refuses while a
# leftover folder exists and says where it is. Delete it (rm -rf), and if that
# run had already added the key's deploy key on GitHub, delete that deploy key
# too, because the leftover private key may be the only other copy of it.
set -euo pipefail
umask 077

OWNER=archievalmariano
SOURCE=$OWNER/dogear

TEMP_ROOT=${TMPDIR:-/tmp}
TEMP_ROOT=${TEMP_ROOT%/}
work=""
cleaned=no

# Every way out runs cleanup exactly once: the private keys go first.
cleanup() {
  if [ "$cleaned" = yes ]; then return 0; fi
  cleaned=yes
  if [ -n "$work" ]; then rm -rf "$work"; fi
  pbcopy < /dev/null 2>/dev/null || true  # the clipboard only ever held a public key
}
on_signal() {  # NAME STATUS
  trap - EXIT INT TERM HUP
  cleanup
  echo
  echo "Stopped by SIG$1: the temporary key folder is deleted."
  echo "Keys this run had finished stay installed; a key in progress may have a deploy key on"
  echo "GitHub but no secret: remove that deploy key before running this again."
  exit "$2"
}
trap cleanup EXIT
trap 'on_signal INT 130' INT
trap 'on_signal TERM 143' TERM
trap 'on_signal HUP 129' HUP

[ -t 0 ] && [ -t 1 ] || { echo "Run this yourself, in an interactive terminal."; exit 2; }
for tool in gh ssh-keygen pbcopy open; do
  command -v "$tool" >/dev/null || { echo "missing: $tool"; exit 2; }
done
for leftover in "$TEMP_ROOT"/dogear-deploy-keys.*; do
  [ -e "$leftover" ] || continue
  echo "A previous run ended abnormally and left private key material in:"
  echo "  $leftover"
  echo "Delete it (rm -rf \"$leftover\"), and delete any deploy key that run added on GitHub, then run this again."
  exit 2
done
gh auth status >/dev/null 2>&1 || { echo "gh is not signed in (gh auth login)"; exit 2; }

work=$(mktemp -d "$TEMP_ROOT/dogear-deploy-keys.XXXXXX")
echo "Temporary key folder (deleted on exit): $work"

# Preflight: the repositories and Environments exist, and nothing is installed yet.
for env in editorial-gate production staging; do
  gh api "repos/$SOURCE/environments/$env" --jq .name >/dev/null || { echo "missing Environment: $env"; exit 2; }
  existing=$(gh api "repos/$SOURCE/environments/$env/secrets" --jq '[.secrets[].name] | join(" ")')
  for name in EDITORIAL_DEPLOY_KEY PUBLICATION_DATA_DEPLOY_KEY; do
    case " $existing " in *" $name "*) echo "$env already has $name; delete it first to rotate"; exit 2;; esac
  done
done
for repo in dogear-editorial dogear-publication-data dogear-publication-data-staging; do
  count=$(gh api "repos/$OWNER/$repo/keys" --jq 'length')
  [ "$count" = 0 ] || { echo "$repo already has $count deploy key(s); remove them first to rotate"; exit 2; }
done

install_key() {  # FILE REPO READ_ONLY(true|false) TITLE SECRET ENV...
  local file=$1 repo=$2 read_only=$3 title=$4 secret=$5
  shift 5
  ssh-keygen -q -t ed25519 -N "" -C "$title" -f "$work/$file"
  local public
  public=$(cut -d' ' -f1,2 < "$work/$file.pub")
  pbcopy < "$work/$file.pub"
  echo
  echo "=== $repo"
  echo "Opening https://github.com/$OWNER/$repo/settings/keys/new"
  echo "  Title:               $title"
  echo "  Key:                 paste (the PUBLIC key is on your clipboard)"
  if [ "$read_only" = true ]; then
    echo "  Allow write access:  leave UNCHECKED"
  else
    echo "  Allow write access:  CHECK it"
  fi
  echo "  then click Add key (GitHub may ask you to confirm with your password or passkey)."
  open "https://github.com/$OWNER/$repo/settings/keys/new"
  while :; do
    read -r -p "Press Return once the key is added (or type q to stop): " answer
    [ "$answer" = q ] && { echo "Stopped. Nothing was stored for $repo; remove its deploy key if you added one."; exit 1; }
    found=$(gh api "repos/$OWNER/$repo/keys" --jq ".[] | select(.key == \"$public\") | .read_only")
    if [ "$found" = "$read_only" ]; then break; fi
    if [ -z "$found" ]; then echo "GitHub does not list this key on $repo yet."
    else echo "The key is there but its access is wrong (read_only=$found); edit or re-add it, then press Return."; fi
  done
  local env
  for env in "$@"; do
    gh secret set "$secret" --repo "$SOURCE" --env "$env" < "$work/$file"
    echo "  stored $secret in the $env Environment"
  done
  rm -f "$work/$file"
  echo "  public fingerprint: $(ssh-keygen -lf "$work/$file.pub" | cut -d' ' -f2) (private key deleted)"
}

install_key editorial dogear-editorial true "dogear-ci editorial (read-only)" \
  EDITORIAL_DEPLOY_KEY editorial-gate production
install_key pubdata-production dogear-publication-data false "dogear-ci production publisher" \
  PUBLICATION_DATA_DEPLOY_KEY production
install_key pubdata-staging dogear-publication-data-staging false "dogear-ci staging publisher" \
  PUBLICATION_DATA_DEPLOY_KEY staging

echo
echo "Done: three deploy keys installed and four Environment secrets stored. No private key remains on this Mac."
