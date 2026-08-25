#!/usr/bin/env bash
# Install the optional financial routing block into a project.
#
# The skills are self-sufficient. Each fin-* skill carries its own rules, its own
# evidence and its own output contract, and needs nothing from this block to do
# its job. What this installs is a short pointer that lives in the files every
# agent reads on every turn, so the right skill is more reliably consulted when a
# task touches money. Uninstalling it costs routing reliability and nothing else.
#
# Usage:
#   scripts/install-guardrails.sh [target-dir]        # default: current directory
#   scripts/install-guardrails.sh --uninstall [dir]
#
# Guarantees, each one proved by scripts/test-install-guardrails.sh:
#
#   1. Temporary files are created with mktemp, in the same verified directory as
#      the file being written, never at a predictable path.
#   2. A target that is a symlink, a non-regular file, or a file with more than
#      one hard link is refused before anything is written. A parent directory
#      that is a symlink or resolves outside the target directory is refused.
#   3. Managed markers must be exactly balanced, one BEGIN and one END, each
#      alone on its line, BEGIN first, with a valid metadata line directly after
#      BEGIN. Anything else is refused rather than guessed at.
#   4. Existing permissions are preserved. Bytes outside the managed block are
#      preserved exactly.
#   5. Each file is replaced by rename(2) from the same directory, so a reader
#      sees the old file or the new one and never a partial write.
#   6. A failure or a signal part way through restores every file this run had
#      already replaced, and removes every file and directory it had created.
#   7. Uninstall never deletes a file that existed before install. It deletes
#      only a file this installer created, which it knows from the metadata line
#      it wrote, and only when nothing but the block is left.
#   8. A complete install then uninstall round trip leaves the host file
#      byte-identical, including a file with no final newline and a zero-byte
#      file.
set -euo pipefail

BEGIN_MARK='<!-- BEGIN financial-engineering-skills -->'
END_MARK='<!-- END financial-engineering-skills -->'
META_PREFIX='<!-- fes-managed-block: host='
META_SUFFIX='; written by scripts/install-guardrails.sh, do not edit -->'
TMP_PREFIX='.fes-guardrails.'

PROG=$(basename "$0")
SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
BLOCK="$SRC_DIR/AGENTS.md"

# Relative names, in the order they are written. AGENTS.md is the canonical file
# read by many tools. CLAUDE.md is read by Claude Code. Copilot ranks
# .github/copilot-instructions.md above AGENTS.md in its own precedence order, so
# it is the only reliable path to Copilot.
NAMES=("AGENTS.md" "CLAUDE.md" ".github/copilot-instructions.md")

FILES=()
EXISTED=()
TOK=()
BL=()
EL=()
ACTION=()
TMPF=()
BAKF=()
MOVED=()
TEMPS=()
CREATED_DIRS=()
SUCCESS=0

die() {
  printf '%s: error: %s\n' "$PROG" "$*" >&2
  exit 1
}

cleanup_temps() {
  local i
  for ((i = 0; i < ${#TEMPS[@]}; i++)); do
    rm -f "${TEMPS[$i]}" 2>/dev/null || true
  done
}

restore_files() {
  local i
  for ((i = ${#FILES[@]} - 1; i >= 0; i--)); do
    if [ "${MOVED[$i]}" -eq 1 ]; then
      if [ -n "${BAKF[$i]}" ] && [ -e "${BAKF[$i]}" ]; then
        mv -f "${BAKF[$i]}" "${FILES[$i]}" 2>/dev/null || true
      else
        rm -f "${FILES[$i]}" 2>/dev/null || true
      fi
      MOVED[i]=0
    fi
  done
}

remove_created_dirs() {
  local i
  for ((i = ${#CREATED_DIRS[@]} - 1; i >= 0; i--)); do
    rmdir "${CREATED_DIRS[$i]}" 2>/dev/null || true
  done
  CREATED_DIRS=()
}

# Restore first, then clear the staging files, then drop any directory this run
# created. The order matters: a directory still holding a staged temporary file
# cannot be removed.
rollback() {
  restore_files
  cleanup_temps
  remove_created_dirs
}

on_signal() {
  trap - INT TERM HUP
  printf '%s: interrupted, rolling back\n' "$PROG" >&2
  rollback
  exit 130
}

on_exit() {
  local rc=$?
  if [ "$SUCCESS" -ne 1 ]; then
    rollback
  else
    cleanup_temps
  fi
  exit "$rc"
}

trap on_signal INT TERM HUP
trap on_exit EXIT

usage() {
  cat <<USAGE
usage: $PROG [--uninstall] [target-dir]

Installs or removes the financial routing block in:
  AGENTS.md, CLAUDE.md, .github/copilot-instructions.md
under target-dir, which defaults to the current directory.
USAGE
}

mode=install
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) mode=uninstall; shift ;;
    -h|--help) usage; SUCCESS=1; exit 0 ;;
    --) shift; break ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -le 1 ] || die "too many arguments"

target_in="${1:-$PWD}"
[ -d "$target_in" ] || die "$target_in is not a directory"
TARGET=$(cd "$target_in" && pwd -P) || die "cannot resolve $target_in"
[ -w "$TARGET" ] || die "$TARGET is not writable"

[ -f "$BLOCK" ] || die "$BLOCK not found"
[ -r "$BLOCK" ] || die "$BLOCK is not readable"

umask_val=$(umask)
DEFAULT_MODE=$(printf '%03o' "$((0666 & ~0${umask_val}))")

# Emit the managed block. The first line of the source file is dropped when it is
# an H1, so the block nests inside a host document. awk terminates every record,
# so a source file without a final newline cannot run into the END marker.
emit_block() {
  printf '%s\n' "$BEGIN_MARK"
  printf '%s%s%s\n' "$META_PREFIX" "$1" "$META_SUFFIX"
  awk 'NR == 1 && /^# / { next } { print }' "$BLOCK"
  printf '%s\n' "$END_MARK"
}

# Count exact-line and substring occurrences of both markers and report the line
# numbers of the first of each. A substring count above the exact-line count means
# a marker is embedded in a longer line, which is ambiguous.
scan_markers() {
  awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
    $0 == b { bx++; if (bl == 0) bl = NR }
    index($0, b) { bs++ }
    $0 == e { ex++; if (el == 0) el = NR }
    index($0, e) { es++ }
    END { printf "%d %d %d %d %d %d\n", bx + 0, bs + 0, ex + 0, es + 0, bl + 0, el + 0 }
  ' "$1"
}

meta_token() {
  local m="$1" t
  case "$m" in
    "$META_PREFIX"*"$META_SUFFIX") : ;;
    *) return 1 ;;
  esac
  t=${m#"$META_PREFIX"}
  t=${t%"$META_SUFFIX"}
  case "$t" in
    absent|empty|lf|nolf) printf '%s' "$t" ;;
    *) return 1 ;;
  esac
}

has_final_newline() {
  local last
  last=$(tail -c 1 "$1" | od -An -tx1 | tr -d ' \n')
  [ "$last" = "0a" ]
}

NEW_TEMP=""
new_temp() {
  local t
  t=$(mktemp "$1/${TMP_PREFIX}XXXXXXXX") || die "cannot create a temporary file in $1"
  TEMPS+=("$t")
  NEW_TEMP="$t"
}

# GNU stat -f means "report on the filesystem" and succeeds, so probing in the
# other order would silently return filesystem statistics instead of a mode.
if stat -c '%a' . >/dev/null 2>&1; then
  STAT_FLAVOUR=gnu
elif stat -f '%Lp' . >/dev/null 2>&1; then
  STAT_FLAVOUR=bsd
else
  STAT_FLAVOUR=none
fi

copy_mode() {
  local src="$1" dst="$2" m
  if cp -p "$src" "$dst" 2>/dev/null; then
    return 0
  fi
  case "$STAT_FLAVOUR" in
    gnu) m=$(stat -c '%a' "$src" 2>/dev/null || true) ;;
    bsd) m=$(stat -f '%Lp' "$src" 2>/dev/null || true) ;;
    *)   m="" ;;
  esac
  if [ -n "$m" ]; then
    chmod "$m" "$dst"
  else
    die "cannot read the permissions of $src"
  fi
}

compose_install() {
  local f="$1" tok="$2" bl="$3" el="$4"
  if [ "$bl" -gt 0 ]; then
    awk -v n="$bl" 'NR < n' "$f"
    emit_block "$tok"
    awk -v n="$el" 'NR > n' "$f"
  else
    if [ -s "$f" ]; then
      cat "$f"
      if [ "$tok" = nolf ]; then printf '\n'; fi
      printf '\n'
    fi
    emit_block "$tok"
  fi
}

compose_uninstall() {
  local f="$1" tok="$2" bl="$3" el="$4" pend lastline
  pend=$((bl - 1))
  if [ "$pend" -ge 1 ]; then
    lastline=$(sed -n "${pend}p" "$f")
    # The blank line directly above BEGIN is the separator this installer added.
    if [ -z "$lastline" ]; then pend=$((pend - 1)); fi
  fi
  if [ "$tok" = nolf ]; then
    { awk -v n="$pend" 'NR <= n' "$f"; awk -v n="$el" 'NR > n' "$f"; } \
      | awk 'NR > 1 { printf "\n" } { printf "%s", $0 }'
  else
    awk -v n="$pend" 'NR <= n' "$f"
    awk -v n="$el" 'NR > n' "$f"
  fi
}

# Phase 1: validate every path and every marker before writing anything.
for name in "${NAMES[@]}"; do
  f="$TARGET/$name"
  d=$(dirname "$f")

  if [ -L "$d" ]; then
    die "$d is a symlink; refusing to write through it"
  fi
  if [ -e "$d" ]; then
    [ -d "$d" ] || die "$d exists and is not a directory"
    pd=$(cd "$d" && pwd -P) || die "cannot resolve $d"
    case "$pd" in
      "$TARGET"|"$TARGET"/*) : ;;
      *) die "$d resolves to $pd, outside $TARGET" ;;
    esac
    [ -w "$d" ] || die "$d is not writable"
  else
    if [ "$mode" = uninstall ]; then
      continue
    fi
  fi

  existed=0
  tok=""
  bl=0
  el=0

  if [ -L "$f" ]; then
    die "$f is a symlink; refusing to write through it. Remove or replace it, then re-run."
  fi
  if [ -e "$f" ]; then
    [ -f "$f" ] || die "$f exists and is not a regular file"
    [ -r "$f" ] || die "$f is not readable"
    [ -w "$f" ] || die "$f is not writable"
    if [ -n "$(find "$f" -maxdepth 0 -links +1 2>/dev/null)" ]; then
      die "$f has more than one hard link; replacing it would silently detach the other name"
    fi
    existed=1

    read -r bx bs ex es bl el <<< "$(scan_markers "$f")"
    # Written as explicit ifs rather than `A && B || die`: in validation code the
    # reader has to be able to see which branch runs, and SC2015 is right that the
    # chained form does not read as if-then-else.
    if [ "$bx" -ne "$bs" ] || [ "$ex" -ne "$es" ]; then
      die "$f: a managed marker appears inside a longer line; refusing to guess"
    fi
    if [ "$bx" -gt 1 ] || [ "$ex" -gt 1 ]; then
      die "$f: duplicate managed markers ($bx BEGIN, $ex END)"
    fi
    [ "$bx" -eq "$ex" ] \
      || die "$f: unbalanced managed markers ($bx BEGIN, $ex END)"
    if [ "$bx" -eq 1 ]; then
      [ "$bl" -lt "$el" ] || die "$f: the END marker precedes the BEGIN marker"
      meta=$(sed -n "$((bl + 1))p" "$f")
      tok=$(meta_token "$meta") \
        || die "$f: the managed block has no valid metadata line at line $((bl + 1))"
    fi
  fi

  if [ "$mode" = install ] && [ -z "$tok" ]; then
    if [ "$existed" -eq 0 ]; then tok=absent
    elif [ ! -s "$f" ]; then tok=empty
    elif has_final_newline "$f"; then tok=lf
    else tok=nolf
    fi
  fi

  if [ "$mode" = uninstall ] && [ "$existed" -eq 1 ] && [ "$bl" -eq 0 ]; then
    printf 'skipped   %s (no managed block)\n' "$f"
    continue
  fi
  if [ "$mode" = uninstall ] && [ "$existed" -eq 0 ]; then
    continue
  fi

  FILES+=("$f")
  EXISTED+=("$existed")
  TOK+=("$tok")
  BL+=("$bl")
  EL+=("$el")
  ACTION+=("$mode")
  TMPF+=("")
  BAKF+=("")
  MOVED+=(0)
done

if [ "${#FILES[@]}" -eq 0 ]; then
  printf 'nothing to do in %s\n' "$TARGET"
  SUCCESS=1
  exit 0
fi

# Phase 2: create any missing parent directory, then stage the new content of
# every file. Nothing in the target is replaced yet.
for ((i = 0; i < ${#FILES[@]}; i++)); do
  f="${FILES[$i]}"
  d=$(dirname "$f")
  if [ ! -d "$d" ]; then
    mkdir -p "$d" || die "cannot create $d"
    CREATED_DIRS+=("$d")
  fi

  if [ "${ACTION[$i]}" = install ]; then
    new_temp "$d"; t="$NEW_TEMP"
    if [ "${EXISTED[$i]}" -eq 1 ]; then copy_mode "$f" "$t"; else chmod "$DEFAULT_MODE" "$t"; fi
    compose_install "$f" "${TOK[$i]}" "${BL[$i]}" "${EL[$i]}" > "$t"
    TMPF[i]="$t"
  else
    new_temp "$d"; t="$NEW_TEMP"
    copy_mode "$f" "$t"
    compose_uninstall "$f" "${TOK[$i]}" "${BL[$i]}" "${EL[$i]}" > "$t"
    TMPF[i]="$t"
    # A file this installer created, or a host file that was empty, is only
    # removed or emptied when the block was genuinely all that was in it.
    if [ ! -s "$t" ]; then
      if [ "${TOK[$i]}" = absent ]; then ACTION[i]=delete; fi
    else
      if [ "${TOK[$i]}" = absent ] || [ "${TOK[$i]}" = empty ]; then
        printf 'note      %s has content outside the block; keeping the file\n' "$f"
      fi
    fi
  fi
done

# Phase 3: back up every file that is about to change, in its own directory, so a
# restore is a rename within one filesystem.
for ((i = 0; i < ${#FILES[@]}; i++)); do
  if [ "${EXISTED[$i]}" -eq 1 ]; then
    new_temp "$(dirname "${FILES[$i]}")"; b="$NEW_TEMP"
    copy_mode "${FILES[$i]}" "$b"
    cat "${FILES[$i]}" > "$b"
    BAKF[i]="$b"
  fi
done

# Phase 4: commit. Each replacement is a rename within one directory.
for ((i = 0; i < ${#FILES[@]}; i++)); do
  f="${FILES[$i]}"
  case "${ACTION[$i]}" in
    delete)
      rm -f "$f"
      MOVED[i]=1
      printf 'removed   %s\n' "$f"
      ;;
    install)
      mv -f "${TMPF[$i]}" "$f"
      MOVED[i]=1
      printf 'installed %s\n' "$f"
      ;;
    uninstall)
      mv -f "${TMPF[$i]}" "$f"
      MOVED[i]=1
      printf 'cleaned   %s\n' "$f"
      ;;
  esac
done

SUCCESS=1
echo
if [ "$mode" = install ]; then
  echo "Routing block installed. Re-run after updating the suite to refresh it."
  echo "Remove it with: $0 --uninstall $TARGET"
else
  echo "Routing block removed. Skills installed under skills/ are unaffected."
fi
