#!/usr/bin/env bash
# Hostile test suite for scripts/install-guardrails.sh.
#
# Runs locally and in CI, on Linux and on macOS, with no dependencies beyond a
# POSIX userland and bash. Every guarantee listed in the installer header has at
# least one case here that fails if the guarantee is removed.
#
#   ./scripts/test-install-guardrails.sh
#
# Failure injection uses PATH shims for mv and mktemp rather than a test hook in
# the installer, so the code under test is exactly the code that ships.
set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
INSTALL="$REPO/scripts/install-guardrails.sh"
BLOCK_SRC="$REPO/AGENTS.md"
BEGIN_MARK='<!-- BEGIN financial-engineering-skills -->'
END_MARK='<!-- END financial-engineering-skills -->'

PASS=0
FAIL=0
CURRENT=""

REAL_MV=$(command -v mv)
REAL_MKTEMP=$(command -v mktemp)

if command -v shasum >/dev/null 2>&1; then
  sha() { shasum -a 256 < "$1" | awk '{print $1}'; }
elif command -v sha256sum >/dev/null 2>&1; then
  sha() { sha256sum < "$1" | awk '{print $1}'; }
else
  echo "no shasum or sha256sum available" >&2
  exit 1
fi

# GNU stat -f means "report on the filesystem" and succeeds, so probing in the
# other order would silently return filesystem statistics instead of a mode.
if stat -c '%a' . >/dev/null 2>&1; then
  mode_of() { stat -c '%a' "$1"; }
else
  mode_of() { stat -f '%Lp' "$1"; }
fi
if stat -c '%i' . >/dev/null 2>&1; then
  inode_of() { stat -c '%i' "$1"; }
else
  inode_of() { stat -f '%i' "$1"; }
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/fes-guardrails-test.XXXXXXXX")
# Resolve symlinks now. On macOS TMPDIR sits under /var, which is a symlink to
# /private/var, and the installer reports the physical path it resolved.
WORK=$(cd "$WORK" && pwd -P)
SHIMDIR="$WORK/shim"
SHIMSTATE="$WORK/shimstate"
mkdir -p "$SHIMDIR" "$SHIMSTATE"
LAST_OUT="$WORK/last-output"
trap 'rm -rf "$WORK"' EXIT

cat > "$SHIMDIR/mv" <<SHIM
#!/usr/bin/env bash
c="\$FES_SHIM_STATE/mv.count"
n=\$(( \$(cat "\$c" 2>/dev/null || echo 0) + 1 ))
echo "\$n" > "\$c"
if [ "\${FES_SHIM_MV_FAIL:-0}" = "\$n" ]; then
  echo "shim: refusing mv call \$n" >&2
  exit 1
fi
if [ "\${FES_SHIM_MV_BLOCK:-0}" = "\$n" ]; then
  echo \$\$ > "\$FES_SHIM_STATE/mv.pid"
  sleep 20
  exit 1
fi
exec "$REAL_MV" "\$@"
SHIM

cat > "$SHIMDIR/mktemp" <<SHIM
#!/usr/bin/env bash
out=\$("$REAL_MKTEMP" "\$@") || exit \$?
printf '%s\n' "\$*" >> "\$FES_SHIM_STATE/mktemp.args"
printf '%s\n' "\$out" >> "\$FES_SHIM_STATE/mktemp.results"
printf '%s\n' "\$out"
SHIM

chmod 755 "$SHIMDIR/mv" "$SHIMDIR/mktemp"

# ---------------------------------------------------------------- test harness

start() { CURRENT="$1"; }
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s: %s\n' "$CURRENT" "$1"; }

check() { # check <description> <condition-as-command...>
  local desc="$1"; shift
  if "$@"; then ok "$desc"; else bad "$desc"; fi
}
check_eq() { # check_eq <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$2', got '$3')"; fi
}
check_ne() {
  if [ "$2" != "$3" ]; then ok "$1"; else bad "$1 (both were '$2')"; fi
}

newproj() { # newproj <name> -> prints the directory
  local d="$WORK/$1"
  rm -rf "$d"
  mkdir -p "$d"
  printf '%s' "$d"
}

run_raw() { # run_raw <command...>; sets RC, captures output in $LAST_OUT
  "$@" > "$LAST_OUT" 2>&1
  RC=$?
  return 0
}

run_install() { # run_install <dir> [extra args...]; sets RC
  local d="$1"; shift
  run_raw "$INSTALL" "$@" "$d"
}

no_temps_left() {
  [ -z "$(find "$1" -name '.fes-guardrails.*' -print 2>/dev/null)" ]
}

block_body() { # the exact bytes the installer should splice in, minus markers
  awk 'NR == 1 && /^# / { next } { print }' "$BLOCK_SRC"
}

extract_block_body() { # extract_block_body <file>: block content minus the two
  awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
    $0 == e { inb = 0 }
    inb && !meta { meta = 1; next }
    inb { print }
    $0 == b { inb = 1 }
  ' "$1"
}

# ---------------------------------------------------------------------- tests

t_every_target_filename() {
  start "every supported target filename"
  local d; d=$(newproj targets)
  run_install "$d"
  check_eq "install exits 0" 0 "$RC"
  local f
  for f in AGENTS.md CLAUDE.md .github/copilot-instructions.md; do
    check "$f exists" test -f "$d/$f"
    check_eq "$f has one BEGIN" 1 "$(grep -c -x -F "$BEGIN_MARK" "$d/$f")"
    check_eq "$f has one END" 1 "$(grep -c -x -F "$END_MARK" "$d/$f")"
    extract_block_body "$d/$f" > "$WORK/got.$$"
    block_body > "$WORK/want.$$"
    check "$f carries the block source verbatim" cmp -s "$WORK/want.$$" "$WORK/got.$$"
  done
  check "no temporary files left" no_temps_left "$d"

  run_install "$d" --uninstall
  check_eq "uninstall exits 0" 0 "$RC"
  for f in AGENTS.md CLAUDE.md .github/copilot-instructions.md; do
    check "$f removed, it was created by the installer" test ! -e "$d/$f"
  done
}

t_roundtrip_byte_identical() {
  start "byte-identical round trip"
  local name shape d before after
  # shape name, then the payload, whose backslash escapes printf %b expands.
  # shellcheck disable=SC2016  # the backticks below are literal markdown, not a command
  local -a shapes=(
    'normal:# Host\n\npara one\n\npara two\n'
    'no-final-newline:# Host\n\npara one'
    'trailing-blanks:# Host\n\npara one\n\n\n\n'
    'single-newline:\n'
    'crlf:# Host\r\n\r\npara one\r\n'
    'unicode:# Hôst\n\nprix 12,50 EUR\n\nfin\n'
    'no-trailing-blank-run:a\nb\nc\n'
    'markdown-fence:# Host\n\n```\nnot a marker\n```\n'
  )
  for shape in "${shapes[@]}"; do
    name=${shape%%:*}
    d=$(newproj "rt-$name")
    printf '%b' "${shape#*:}" > "$d/AGENTS.md"
    before=$(sha "$d/AGENTS.md")
    run_install "$d"
    check_eq "$name: install exits 0" 0 "$RC"
    check_ne "$name: block was added" "$before" "$(sha "$d/AGENTS.md")"
    run_install "$d" --uninstall
    check_eq "$name: uninstall exits 0" 0 "$RC"
    after=$(sha "$d/AGENTS.md")
    check_eq "$name: host file is byte-identical" "$before" "$after"
    check "$name: no temporary files left" no_temps_left "$d"
  done
}

t_empty_file_survives() {
  start "uninstall does not delete a pre-existing empty file"
  local d; d=$(newproj empty)
  : > "$d/AGENTS.md"
  chmod 600 "$d/AGENTS.md"
  run_install "$d"
  check_eq "install exits 0" 0 "$RC"
  check "block was written into the empty file" grep -q -x -F "$BEGIN_MARK" "$d/AGENTS.md"
  run_install "$d" --uninstall
  check_eq "uninstall exits 0" 0 "$RC"
  check "the pre-existing empty file still exists" test -f "$d/AGENTS.md"
  check_eq "it is zero bytes again" 0 "$(wc -c < "$d/AGENTS.md" | tr -d ' ')"
  check_eq "its mode is preserved" 600 "$(mode_of "$d/AGENTS.md")"
}

t_modes_preserved() {
  start "file modes"
  local d; d=$(newproj modes)
  printf 'host\n' > "$d/AGENTS.md"; chmod 600 "$d/AGENTS.md"
  printf 'host\n' > "$d/CLAUDE.md"; chmod 640 "$d/CLAUDE.md"
  run_install "$d"
  check_eq "install exits 0" 0 "$RC"
  check_eq "0600 preserved" 600 "$(mode_of "$d/AGENTS.md")"
  check_eq "0640 preserved" 640 "$(mode_of "$d/CLAUDE.md")"
  run_install "$d" --uninstall
  check_eq "0600 preserved through uninstall" 600 "$(mode_of "$d/AGENTS.md")"
  check_eq "0640 preserved through uninstall" 640 "$(mode_of "$d/CLAUDE.md")"

  # A file the installer creates takes the mode the caller's umask implies.
  d=$(newproj modes-new)
  ( umask 077; "$INSTALL" "$d" >/dev/null 2>&1 )
  check_eq "new file under umask 077 is 0600" 600 "$(mode_of "$d/AGENTS.md")"
  d=$(newproj modes-new-022)
  ( umask 022; "$INSTALL" "$d" >/dev/null 2>&1 )
  check_eq "new file under umask 022 is 0644" 644 "$(mode_of "$d/AGENTS.md")"
}

t_unrelated_bytes_preserved() {
  start "unrelated bytes"
  local d; d=$(newproj unrelated)
  printf 'prefix line\n' > "$d/AGENTS.md"
  run_install "$d"
  printf 'suffix line\n' >> "$d/AGENTS.md"
  local before; before=$(sha "$d/AGENTS.md")
  run_install "$d"
  check_eq "reinstall exits 0" 0 "$RC"
  check_eq "reinstall changes nothing when the block is current" "$before" "$(sha "$d/AGENTS.md")"
  run_install "$d" --uninstall
  check_eq "uninstall exits 0" 0 "$RC"
  printf 'prefix line\nsuffix line\n' > "$WORK/want-unrelated"
  check "content on both sides of the block survives" cmp -s "$WORK/want-unrelated" "$d/AGENTS.md"
}

t_repeated_installation() {
  start "repeated installation"
  local d; d=$(newproj repeat)
  printf '# Host\n\nbody\n' > "$d/AGENTS.md"
  run_install "$d"
  local a; a=$(sha "$d/AGENTS.md")
  run_install "$d"
  local b; b=$(sha "$d/AGENTS.md")
  run_install "$d"
  local c; c=$(sha "$d/AGENTS.md")
  check_eq "second install is byte-identical" "$a" "$b"
  check_eq "third install is byte-identical" "$a" "$c"
  check_eq "still exactly one BEGIN" 1 "$(grep -c -x -F "$BEGIN_MARK" "$d/AGENTS.md")"
  check_eq "still exactly one END" 1 "$(grep -c -x -F "$END_MARK" "$d/AGENTS.md")"
  check "no temporary files left" no_temps_left "$d"
}

t_malformed_markers() {
  start "malformed markers"
  local case_name payload d before
  local -a cases=(
    "begin-without-end:$BEGIN_MARK"
    "end-without-begin:$END_MARK"
    "end-before-begin:$END_MARK\nmiddle\n$BEGIN_MARK"
    "begin-embedded:text $BEGIN_MARK trailing\n$END_MARK"
    "begin-with-cr:$BEGIN_MARK\r\n$END_MARK"
    "block-without-metadata:$BEGIN_MARK\nbody\n$END_MARK"
    "block-with-bad-metadata:$BEGIN_MARK\n<!-- fes-managed-block: host=wat; written by scripts/install-guardrails.sh, do not edit -->\n$END_MARK"
  )
  for case_name in "${cases[@]}"; do
    payload=${case_name#*:}
    case_name=${case_name%%:*}
    d=$(newproj "marker-$case_name")
    printf '%b\n' "$payload" > "$d/AGENTS.md"
    before=$(sha "$d/AGENTS.md")
    run_install "$d"
    check_ne "$case_name: install refuses" 0 "$RC"
    check_eq "$case_name: the host file is untouched" "$before" "$(sha "$d/AGENTS.md")"
    check "$case_name: no other target was written" test ! -e "$d/CLAUDE.md"
    check "$case_name: no temporary files left" no_temps_left "$d"
    run_install "$d" --uninstall
    check_ne "$case_name: uninstall refuses too" 0 "$RC"
    check_eq "$case_name: still untouched after uninstall" "$before" "$(sha "$d/AGENTS.md")"
  done
}

t_duplicate_markers() {
  start "duplicate markers"
  local d before
  d=$(newproj dup-both)
  printf '%s\nbody\n%s\ntail\n%s\nbody\n%s\n' \
    "$BEGIN_MARK" "$END_MARK" "$BEGIN_MARK" "$END_MARK" > "$d/AGENTS.md"
  before=$(sha "$d/AGENTS.md")
  run_install "$d"
  check_ne "two blocks: install refuses" 0 "$RC"
  check_eq "two blocks: file untouched" "$before" "$(sha "$d/AGENTS.md")"

  d=$(newproj dup-begin)
  printf '%s\n%s\nbody\n%s\n' "$BEGIN_MARK" "$BEGIN_MARK" "$END_MARK" > "$d/AGENTS.md"
  before=$(sha "$d/AGENTS.md")
  run_install "$d"
  check_ne "two BEGIN one END: install refuses" 0 "$RC"
  check_eq "two BEGIN one END: file untouched" "$before" "$(sha "$d/AGENTS.md")"
  check "no temporary files left" no_temps_left "$d"
}

t_symlink_target() {
  start "symlink targets"
  local d; d=$(newproj symlink-file)
  mkdir -p "$d/outside"
  printf 'secret\n' > "$d/outside/victim"
  local before; before=$(sha "$d/outside/victim")
  ln -s "$d/outside/victim" "$d/AGENTS.md"
  run_install "$d"
  check_ne "a symlinked target is refused" 0 "$RC"
  check_eq "the symlink's target is untouched" "$before" "$(sha "$d/outside/victim")"
  check "AGENTS.md is still a symlink" test -L "$d/AGENTS.md"
  check "no other target was written" test ! -e "$d/CLAUDE.md"

  # The CLAUDE.md to AGENTS.md symlink convention must also be refused rather
  # than followed, which would write the block into AGENTS.md twice.
  d=$(newproj symlink-claude)
  printf '# Host\n' > "$d/AGENTS.md"
  ln -s AGENTS.md "$d/CLAUDE.md"
  before=$(sha "$d/AGENTS.md")
  run_install "$d"
  check_ne "a symlinked CLAUDE.md is refused" 0 "$RC"
  check_eq "AGENTS.md is untouched" "$before" "$(sha "$d/AGENTS.md")"
  check "no temporary files left" no_temps_left "$d"
}

t_symlink_parent() {
  start "symlinked parent directory"
  local d; d=$(newproj symlink-parent)
  mkdir -p "$d/outside"
  ln -s "$d/outside" "$d/.github"
  run_install "$d"
  check_ne ".github as a symlink is refused" 0 "$RC"
  check "nothing was written through it" test ! -e "$d/outside/copilot-instructions.md"
  check "no target file was written" test ! -e "$d/AGENTS.md"
}

t_predictable_temp_symlink() {
  start "predictable temp name is never used"
  local d; d=$(newproj temp-symlink)
  mkdir -p "$d/outside"
  printf 'secret\n' > "$d/outside/victim"
  local before; before=$(sha "$d/outside/victim")
  # The old installer wrote to "$f.tmp". Pre-create that path as a symlink.
  ln -s "$d/outside/victim" "$d/AGENTS.md.tmp"
  ln -s "$d/outside/victim" "$d/CLAUDE.md.tmp"
  run_install "$d"
  check_eq "install still succeeds" 0 "$RC"
  check_eq "the pre-created temp symlink target is untouched" "$before" "$(sha "$d/outside/victim")"
  check "AGENTS.md.tmp is still a symlink" test -L "$d/AGENTS.md.tmp"
  check "AGENTS.md was written" grep -q -x -F "$BEGIN_MARK" "$d/AGENTS.md"
}

t_non_regular_targets() {
  start "non-regular targets"
  local d; d=$(newproj fifo)
  mkfifo "$d/AGENTS.md"
  run_install "$d"
  check_ne "a FIFO target is refused" 0 "$RC"
  check "it is still a FIFO" test -p "$d/AGENTS.md"
  check "no other target was written" test ! -e "$d/CLAUDE.md"

  d=$(newproj dir-target)
  mkdir -p "$d/AGENTS.md"
  run_install "$d"
  check_ne "a directory target is refused" 0 "$RC"
  check "it is still a directory" test -d "$d/AGENTS.md"
}

t_hard_links() {
  start "hard links"
  local d; d=$(newproj hardlink)
  printf 'shared content\n' > "$d/other-name"
  ln "$d/other-name" "$d/AGENTS.md"
  local before; before=$(sha "$d/other-name")
  run_install "$d"
  check_ne "a multiply linked target is refused" 0 "$RC"
  check_eq "the other name is untouched" "$before" "$(sha "$d/other-name")"
  check_eq "the target is untouched" "$before" "$(sha "$d/AGENTS.md")"
  check "no other target was written" test ! -e "$d/CLAUDE.md"
  check "no temporary files left" no_temps_left "$d"
}

t_temp_files_are_safe() {
  start "temporary files are unique and inside the destination"
  local d; d=$(newproj tempsafe)
  rm -f "$SHIMSTATE/mktemp.args" "$SHIMSTATE/mktemp.results" "$SHIMSTATE/mv.count"
  PATH="$SHIMDIR:$PATH" FES_SHIM_STATE="$SHIMSTATE" "$INSTALL" "$d" >/dev/null 2>&1
  check_eq "install exits 0" 0 "$?"
  check "mktemp was used" test -s "$SHIMSTATE/mktemp.args"
  local bad_arg=0 line
  while IFS= read -r line; do
    case "$line" in
      "$d"/*XXXXXXXX|"$d"/.github/*XXXXXXXX) : ;;
      *) bad_arg=1; printf '    unexpected mktemp template: %s\n' "$line" ;;
    esac
  done < "$SHIMSTATE/mktemp.args"
  check_eq "every temp template is inside the destination and randomised" 0 "$bad_arg"
  # The template is deliberately the same for two files in one directory. What
  # must never repeat, and must never be predictable, is the name mktemp returns.
  local uniq_count total_count bad_result=0
  total_count=$(wc -l < "$SHIMSTATE/mktemp.results" | tr -d ' ')
  uniq_count=$(sort -u "$SHIMSTATE/mktemp.results" | wc -l | tr -d ' ')
  check_eq "every created temporary name is distinct" "$total_count" "$uniq_count"
  while IFS= read -r line; do
    case "$line" in
      "$d"/.fes-guardrails.????????|"$d"/.github/.fes-guardrails.????????) : ;;
      *) bad_result=1; printf '    unexpected temporary file: %s\n' "$line" ;;
    esac
    case "$line" in
      *AGENTS.md.tmp|*CLAUDE.md.tmp|*copilot-instructions.md.tmp) bad_result=1 ;;
    esac
  done < "$SHIMSTATE/mktemp.results"
  check_eq "no temporary file used a predictable name" 0 "$bad_result"
  check "no temporary files left" no_temps_left "$d"
}

t_atomic_replacement() {
  start "atomic replacement"
  local d; d=$(newproj atomic)
  printf 'host\n' > "$d/AGENTS.md"
  local before_inode; before_inode=$(inode_of "$d/AGENTS.md")
  run_install "$d"
  local after_inode; after_inode=$(inode_of "$d/AGENTS.md")
  check_ne "the file was replaced by rename, not written in place" "$before_inode" "$after_inode"
  check "no temporary files left" no_temps_left "$d"
}

t_partial_failure_rollback() {
  start "partial failure rolls back every target"
  local d; d=$(newproj partial)
  printf '# A\n\nalpha\n' > "$d/AGENTS.md"; chmod 600 "$d/AGENTS.md"
  printf '# C\n\ncharlie\n' > "$d/CLAUDE.md"; chmod 640 "$d/CLAUDE.md"
  local a_before c_before
  a_before=$(sha "$d/AGENTS.md")
  c_before=$(sha "$d/CLAUDE.md")
  rm -f "$SHIMSTATE/mv.count"
  PATH="$SHIMDIR:$PATH" FES_SHIM_STATE="$SHIMSTATE" FES_SHIM_MV_FAIL=3 \
    "$INSTALL" "$d" >/dev/null 2>&1
  local rc=$?
  check_ne "the run fails" 0 "$rc"
  check_eq "AGENTS.md is restored byte-for-byte" "$a_before" "$(sha "$d/AGENTS.md")"
  check_eq "CLAUDE.md is restored byte-for-byte" "$c_before" "$(sha "$d/CLAUDE.md")"
  check_eq "AGENTS.md keeps its mode" 600 "$(mode_of "$d/AGENTS.md")"
  check_eq "CLAUDE.md keeps its mode" 640 "$(mode_of "$d/CLAUDE.md")"
  check "the third target was not left behind" test ! -e "$d/.github/copilot-instructions.md"
  check "the directory the run created was removed" test ! -d "$d/.github"
  check "no temporary files left" no_temps_left "$d"

  # Same failure, but with no pre-existing files: rollback must delete what the
  # run created rather than restore it.
  d=$(newproj partial-fresh)
  rm -f "$SHIMSTATE/mv.count"
  PATH="$SHIMDIR:$PATH" FES_SHIM_STATE="$SHIMSTATE" FES_SHIM_MV_FAIL=3 \
    "$INSTALL" "$d" >/dev/null 2>&1
  rc=$?
  check_ne "the fresh run fails" 0 "$rc"
  check "AGENTS.md was removed again" test ! -e "$d/AGENTS.md"
  check "CLAUDE.md was removed again" test ! -e "$d/CLAUDE.md"
  check "the target directory is empty again" test -z "$(ls -A "$d")"
}

t_interruption() {
  start "interruption rolls back"
  local d; d=$(newproj interrupt)
  printf '# A\n\nalpha\n' > "$d/AGENTS.md"
  printf '# C\n\ncharlie\n' > "$d/CLAUDE.md"
  local a_before c_before
  a_before=$(sha "$d/AGENTS.md")
  c_before=$(sha "$d/CLAUDE.md")
  rm -f "$SHIMSTATE/mv.count" "$SHIMSTATE/mv.pid"

  PATH="$SHIMDIR:$PATH" FES_SHIM_STATE="$SHIMSTATE" FES_SHIM_MV_BLOCK=2 \
    "$INSTALL" "$d" >/dev/null 2>&1 &
  local pid=$!
  local waited=0
  while [ ! -s "$SHIMSTATE/mv.pid" ] && [ "$waited" -lt 100 ]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  if [ ! -s "$SHIMSTATE/mv.pid" ]; then
    bad "the run never reached the second replacement"
    kill -TERM "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    return
  fi
  kill -TERM "$pid" 2>/dev/null
  local shim_pid; shim_pid=$(cat "$SHIMSTATE/mv.pid")
  pkill -P "$shim_pid" 2>/dev/null
  kill -TERM "$shim_pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  local rc=$?
  check_ne "the interrupted run does not report success" 0 "$rc"
  check_eq "AGENTS.md is restored byte-for-byte" "$a_before" "$(sha "$d/AGENTS.md")"
  check_eq "CLAUDE.md is restored byte-for-byte" "$c_before" "$(sha "$d/CLAUDE.md")"
  check "no third target was left behind" test ! -e "$d/.github/copilot-instructions.md"
  check "no temporary files left" no_temps_left "$d"
}

t_uninstall_leaves_foreign_files() {
  start "uninstall leaves files it does not manage"
  local d; d=$(newproj foreign)
  printf '# Host\n\nnothing of ours here\n' > "$d/AGENTS.md"
  chmod 600 "$d/AGENTS.md"
  local before; before=$(sha "$d/AGENTS.md")
  run_install "$d" --uninstall
  check_eq "uninstall exits 0" 0 "$RC"
  check "the file still exists" test -f "$d/AGENTS.md"
  check_eq "the file is untouched" "$before" "$(sha "$d/AGENTS.md")"
  check_eq "the mode is untouched" 600 "$(mode_of "$d/AGENTS.md")"
  check "no CLAUDE.md was invented" test ! -e "$d/CLAUDE.md"
  check "no .github was invented" test ! -d "$d/.github"

  d=$(newproj empty-dir)
  run_install "$d" --uninstall
  check_eq "uninstall of an untouched directory exits 0" 0 "$RC"
  check "it stays empty" test -z "$(ls -A "$d")"
}

t_uninstall_keeps_added_content() {
  start "uninstall keeps content added to a file the installer created"
  local d; d=$(newproj added)
  run_install "$d"
  printf 'a line the user added later\n' >> "$d/CLAUDE.md"
  run_install "$d" --uninstall
  check_eq "uninstall exits 0" 0 "$RC"
  check "the file the user wrote into is kept" test -f "$d/CLAUDE.md"
  printf 'a line the user added later\n' > "$WORK/want-added"
  check "only the block was removed" cmp -s "$WORK/want-added" "$d/CLAUDE.md"
  check "the untouched file is still removed" test ! -e "$d/AGENTS.md"
}

t_awkward_paths() {
  start "awkward target paths"
  local d; d=$(newproj "a dir with spaces & 'quotes'")
  printf '# Host\n\nbody\n' > "$d/AGENTS.md"
  local before; before=$(sha "$d/AGENTS.md")
  run_install "$d"
  check_eq "a path with spaces and quotes installs" 0 "$RC"
  check "the block landed" grep -q -x -F "$BEGIN_MARK" "$d/AGENTS.md"
  run_install "$d" --uninstall
  check_eq "and uninstalls" 0 "$RC"
  check_eq "byte-identical afterwards" "$before" "$(sha "$d/AGENTS.md")"

  # A relative path, and the default of no path at all, both resolve to the same place.
  d=$(newproj relative)
  printf 'body\n' > "$d/AGENTS.md"
  before=$(sha "$d/AGENTS.md")
  ( cd "$d" && "$INSTALL" . >/dev/null 2>&1 )
  check "a relative target installs" grep -q -x -F "$BEGIN_MARK" "$d/AGENTS.md"
  ( cd "$d" && "$INSTALL" --uninstall >/dev/null 2>&1 )
  check_eq "the default target is the working directory" "$before" "$(sha "$d/AGENTS.md")"
}

t_bad_arguments() {
  start "argument handling"
  run_raw "$INSTALL" "$WORK/does-not-exist"
  check_ne "a missing directory is refused" 0 "$RC"
  run_raw "$INSTALL" --nope
  check_ne "an unknown option is refused" 0 "$RC"
  local d; d=$(newproj args)
  run_raw "$INSTALL" "$d" "$d"
  check_ne "two directories are refused" 0 "$RC"
  check "nothing was written" test ! -e "$d/AGENTS.md"
}

# ----------------------------------------------------------------------- main

printf 'install-guardrails hostile suite\n'
printf 'platform: %s %s, bash %s\n' "$(uname -s)" "$(uname -m)" "${BASH_VERSION}"
printf 'work dir: %s\n\n' "$WORK"

for t in \
  t_every_target_filename \
  t_roundtrip_byte_identical \
  t_empty_file_survives \
  t_modes_preserved \
  t_unrelated_bytes_preserved \
  t_repeated_installation \
  t_malformed_markers \
  t_duplicate_markers \
  t_symlink_target \
  t_symlink_parent \
  t_predictable_temp_symlink \
  t_non_regular_targets \
  t_hard_links \
  t_temp_files_are_safe \
  t_atomic_replacement \
  t_partial_failure_rollback \
  t_interruption \
  t_uninstall_leaves_foreign_files \
  t_uninstall_keeps_added_content \
  t_awkward_paths \
  t_bad_arguments
do
  printf '%s\n' "$t"
  "$t"
done

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
