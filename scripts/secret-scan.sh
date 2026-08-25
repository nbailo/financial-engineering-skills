#!/usr/bin/env bash
# Scan this repository for committed credentials.
#
# Two jobs, deliberately separated.
#
#   Once, before a release, a human runs the full-history scan. Rewriting history is the only
#   way to remove a secret from a public repository, and the only cheap moment to discover one
#   is before the tag exists. This mode reads every commit reachable from every local ref, so
#   it costs minutes rather than the seconds a per-push scan is allowed.
#
#   On every push and pull request, CI scans the changed range only. A push-time scan that
#   re-read the whole history would either be slow enough to be turned off or shallow enough
#   to be theatre; scanning what the push actually adds is the part that can still be fixed
#   before it is published.
#
# What it reports: the location and the name of the pattern that matched, never the matched
# text. Printing the candidate secret into a CI log would copy it somewhere with different
# retention and different access rules.
#
# What it does not do. It matches known credential shapes, so it finds a leaked GitHub token
# or a PEM private key, and it does not find a high-entropy string with no recognisable
# format, an exchange API key that is a bare hex blob, or a secret split across lines. A clean
# run is evidence about these patterns over this range, and nothing wider. It reads commits
# reachable from local refs: objects that exist only on the remote, or only in a reflog, are
# not scanned. GitHub secret scanning push protection, where a repository has it turned on,
# is the control that blocks provider-issued patterns at push time. This script is the part
# that runs before a tag and the part that fails a pull request. SECURITY.md records which of
# those settings this repository had turned on, and on which date they were read.
#
# Usage:
#   scripts/secret-scan.sh                 every commit reachable from any ref, plus the tree
#   scripts/secret-scan.sh --history       the same, stated explicitly
#   scripts/secret-scan.sh --range A..B    only the lines added in that commit range
#   scripts/secret-scan.sh --tree          only the files currently tracked
#
# Exit status: 0 nothing matched, 1 at least one match, 2 the arguments or the range are wrong.
set -euo pipefail

# name<space>extended-regular-expression. Case sensitive: these prefixes are issued in a fixed
# case, and matching them loosely would report every mention of the word in prose.
PATTERNS_CS=(
  'pem-private-key -----BEGIN [A-Z ]*PRIVATE KEY-----'
  'github-token gh[pousr]_[A-Za-z0-9]{36}'
  'github-fine-grained-pat github_pat_[A-Za-z0-9_]{60}'
  'aws-access-key-id (AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}'
  'slack-token xox[baprs]-[A-Za-z0-9-]{10}'
  'stripe-live-key (sk|rk)_live_[A-Za-z0-9]{16}'
  'google-api-key AIza[A-Za-z0-9_-]{35}'
  'anthropic-api-key sk-ant-[A-Za-z0-9_-]{20}'
  'openai-project-key sk-proj-[A-Za-z0-9_-]{20}'
  'npm-token npm_[A-Za-z0-9]{36}'
  'pypi-token pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20}'
  'json-web-token eyJ[A-Za-z0-9_-]{10}\.eyJ[A-Za-z0-9_-]{10}\.'
  'putty-private-key PuTTY-User-Key-File-[0-9]'
)

# Case insensitive: a credential assigned to a name that says what it is. Two things keep the
# documentation out of the report: the value must be at least 24 characters, and it must be a
# single run of credential characters, so `api_key = os.environ["STRIPE_SECRET_KEY"]` is not a
# finding while `api_key = "<24 or more characters>"` is. The leading [[:punct:]] allows the
# opening quote. A secret assembled by code, rather than written down, is out of reach here.
PATTERNS_CI=(
  'assigned-credential (api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|client[_-]?secret|password)[[:alnum:]_]*[[:space:]]*[=:][[:space:]]*[[:punct:]]{0,2}[A-Za-z0-9/+=_.-]{24,}'
  'assigned-signing-key (private[_-]?key|privkey|mnemonic|seed[_-]?phrase)[^[:alnum:]]{1,6}(0x)?[0-9a-f]{64}'
)

usage() {
  sed -n '2,40p' "$0" >&2
  exit 2
}

mode=history
range=""
case "${1:-}" in
  ""|--history) mode=history ;;
  --tree)       mode=tree ;;
  --range)      mode=range; range="${2:-}"; [ -n "$range" ] || usage ;;
  *)            usage ;;
esac

cd "$(git rev-parse --show-toplevel)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
stream="$tmp/stream.tsv"

# Field 1 is the location, field 2 onwards is the line. Only field 1 is ever printed.
# shellcheck disable=SC2016  # $0 and $2 belong to awk here and must not be expanded by the shell
added_lines_awk='
  /^commit [0-9a-f]/ { c = substr($2, 1, 12); next }
  /^\+\+\+ / { f = $2; sub(/^b\//, "", f); next }
  /^\+/ { print "commit " c " " f "\t" substr($0, 2) }
'

case "$mode" in
  history)
    git log --all --no-color --format='commit %H' -p | awk "$added_lines_awk" > "$stream"
    scanned="every commit reachable from a local ref, plus the tracked tree"
    ;;
  range)
    for end in "${range%%..*}" "${range##*..}"; do
      if ! git rev-parse --verify --quiet "${end}^{commit}" >/dev/null; then
        echo "secret-scan: '$end' is not a commit in this clone." >&2
        echo "  A range whose endpoints are missing is a hole, not an empty result." >&2
        echo "  Fetch the range (actions/checkout with fetch-depth: 0) and run it again." >&2
        exit 2
      fi
    done
    git log --no-color --format='commit %H' -p "$range" | awk "$added_lines_awk" > "$stream"
    scanned="lines added in $range"
    ;;
  tree)
    scanned="the tracked tree"
    ;;
esac

if [ "$mode" = "history" ] || [ "$mode" = "tree" ]; then
  # Tracked files as they stand. In history mode this catches a secret that was committed
  # before this script existed and still sits in the checkout.
  while IFS= read -r -d '' f; do
    [ -f "$f" ] || continue
    LC_ALL=C grep -Iq . "$f" 2>/dev/null || continue   # skip binaries and empty files
    awk -v p="$f" '{ print p ":" FNR "\t" $0 }' "$f" >> "$stream"
  done < <(git ls-files -z)
fi

[ -s "$stream" ] || touch "$stream"

findings=0
report() {   # $1 = "case-sensitive" or "case-insensitive", $2 = "name regex"
  local entry name regex hits where
  entry="$2"
  name="${entry%% *}"
  regex="${entry#* }"
  if [ "$1" = "case-insensitive" ]; then
    hits="$(LC_ALL=C grep -i -E -- "$regex" "$stream" | cut -f1 | sort -u || true)"
  else
    hits="$(LC_ALL=C grep -E -- "$regex" "$stream" | cut -f1 | sort -u || true)"
  fi
  [ -n "$hits" ] || return 0
  while IFS= read -r where; do
    [ -n "$where" ] || continue
    echo "  $name  $where"
    findings=$((findings + 1))
  done <<< "$hits"
}

for entry in "${PATTERNS_CS[@]}"; do report case-sensitive "$entry"; done
for entry in "${PATTERNS_CI[@]}"; do report case-insensitive "$entry"; done

echo
if [ "$findings" -gt 0 ]; then
  echo "secret-scan: $findings match(es) over $scanned."
  echo "The matched text is deliberately not printed. Open each location, and if it is a real"
  echo "credential, rotate it first: it is public from the moment it is pushed, and removing"
  echo "the commit does not un-publish it."
  exit 1
fi
echo "secret-scan: no match over $scanned."
echo "Patterns checked: ${#PATTERNS_CS[@]} literal-prefix, ${#PATTERNS_CI[@]} assignment."
echo "That is evidence about these patterns over this range, and nothing wider."
