#!/usr/bin/env bash
# Install the always-on financial guardrails into a project.
#
# Skills are loaded only when an agent decides they are relevant. For a
# correctness suite that is the wrong default: the agent believes it can already
# write the money-handling code, so it never loads the skill. The guardrails
# block closes that gap by living in the files every agent reads on every turn.
#
# Idempotent: re-running replaces the block between the markers, never appends a
# second copy. Removing it is `--uninstall`.
#
# Usage:
#   scripts/install-guardrails.sh [target-dir]      # default: current directory
#   scripts/install-guardrails.sh --uninstall [dir]
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLOCK="$SRC_DIR/AGENTS.md"
BEGIN='<!-- BEGIN financial-engineering-skills -->'
END='<!-- END financial-engineering-skills -->'

uninstall=0
if [ "${1:-}" = "--uninstall" ]; then uninstall=1; shift; fi
TARGET="${1:-$PWD}"

[ -f "$BLOCK" ] || { echo "error: $BLOCK not found" >&2; exit 1; }
[ -d "$TARGET" ] || { echo "error: $TARGET is not a directory" >&2; exit 1; }

# AGENTS.md is the canonical file (read by 25+ tools). CLAUDE.md is read by
# Claude Code. .github/copilot-instructions.md outranks AGENTS.md in Copilot's
# own precedence order, so it is the only reliable path to Copilot.
FILES=("$TARGET/AGENTS.md" "$TARGET/CLAUDE.md" "$TARGET/.github/copilot-instructions.md")

strip_block() {
  # Delete an existing marked block, if present. Uses awk so it works the same
  # on BSD and GNU userlands.
  awk -v b="$BEGIN" -v e="$END" '
    index($0, b) { skip = 1 }
    !skip { print }
    index($0, e) { skip = 0 }
  ' "$1"
}

for f in "${FILES[@]}"; do
  mkdir -p "$(dirname "$f")"

  existing=""
  if [ -f "$f" ]; then
    # Command substitution already strips trailing newlines, which is exactly the
    # normalisation needed so repeated runs do not accumulate blank lines.
    # (Do not "improve" this with awk RS="\0": BSD awk reads that as paragraph
    # mode and silently collapses the host file's blank lines.)
    existing="$(strip_block "$f")"
  fi

  if [ "$uninstall" -eq 1 ]; then
    if [ -z "$existing" ]; then rm -f "$f"; echo "removed  $f"
    else printf '%s\n' "$existing" > "$f"; echo "cleaned  $f"; fi
    continue
  fi

  {
    if [ -n "$existing" ]; then printf '%s\n\n' "$existing"; fi
    printf '%s\n' "$BEGIN"
    # Skip the source file's own H1 so it nests cleanly inside a host document.
    sed '1{/^# /d;}' "$BLOCK"
    printf '%s\n' "$END"
  } > "$f.tmp" && mv "$f.tmp" "$f"

  echo "installed $f"
done

if [ "$uninstall" -eq 1 ]; then
  echo
  echo "Guardrails removed. Skills installed under skills/ are unaffected."
else
  echo
  echo "Guardrails installed. They are now in context on every agent turn."
  echo "Re-run this script after updating the suite to refresh the block."
fi
