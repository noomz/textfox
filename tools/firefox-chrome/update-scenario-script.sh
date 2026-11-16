#!/bin/sh
# NixOS VM scenario: two wrapped Firefox generations against one profile.
# What a refusal says is checked by the node harness (Cu.reportError output does not
# reach the process log); here only what a refusal leaves behind is checked.
# Phases: A fresh install; B generation switch converges by itself; C leftovers
# from an interrupted run are swept; D a manual profile is refused untouched and
# Firefox keeps running; E an edited owned file is refused untouched and then
# converges under textfox.force-adopt.
#
# Constraints this file has to live with: the unit runs with systemd's minimal
# PATH, so only coreutils/diffutils-free binaries plus shell builtins are available
# (no ps, no pkill, no setsid, no cmp); and a run that replaces stylesheets restarts
# Firefox, leaving an instance that outlives the run that started it, so every
# start is followed by a sweep for the profile's processes through /proc.
set -u
shared=/tmp/shared
p="$HOME/pa"
log="$shared/firefox.log"
prog="$shared/progress"
trace="$shared/unit.log"
step() { echo "$1 $(date +%s)" >> "$prog" 2>> "$trace" || retry_shared; }
retry_shared() {
  rj=1
  while [ "$rj" -le 30 ]; do
    if echo "mount-wait $rj $(date +%s)" >> "$prog" 2>> "$trace"; then return 0; fi
    sleep 2
    rj=$((rj+1))
  done
  systemctl poweroff
  exit 1
}
retry_shared

exec 2>> "$trace"
set -x

# Every variable below is global (this runs under plain sh), so each helper owns
# its own names: a helper that reuses another's counter would silently reset it.
# A phase that hangs would otherwise keep the VM alive until the outer timeout,
# which reports nothing. The watchdog always ends the VM, so the artifacts on the
# host always describe where the run got to.
(
  sleep 600
  echo "watchdog: run never finished" >> "$prog"
  systemctl poweroff
) &
watchdog=$!

# Kill every process whose command line names this profile, including the
# instance Firefox spawned to restart itself after installing a generation.
killff() {
  for kround in 1 2 3 4 5 6 7 8 9 10; do
    kfound=0
    for kdir in /proc/[0-9]*; do
      kpid=${kdir#/proc/}
      [ "$kpid" = "$$" ] && continue
      kcmd=$(tr '\0' ' ' < "$kdir/cmdline" 2>/dev/null) || continue
      case $kcmd in
        *"--profile $1 "*) kill -9 "$kpid" 2>/dev/null && kfound=1 ;;
      esac
    done
    [ "$kfound" = 0 ] && break
    sleep 1
  done
  sleep 1
  rm -f "$1"/lock "$1"/.parentlock 2>/dev/null
}

# Bounded poll for a process that may have been reparented or reaped already;
# the wait(1) builtin can block forever on a pid the shell no longer owns.
gone() {
  gi=0
  while [ "$gi" -lt 5 ]; do
    kill -0 "$1" 2>/dev/null || return 0
    sleep 1
    gi=$((gi+1))
  done
  return 1
}

# One Firefox start, waited for and then cleaned up. The install may restart
# Firefox, so the wait ends at the first process and the sweep ends the rest.
runff() {
  echo "run $(basename "$1") $(date +%s)" >> "$log" 2>/dev/null
  "$1"/bin/firefox --new-instance --profile "$2" about:blank >> "$log" 2>&1 &
  ffpid=$!
  fi=0
  while [ "$fi" -lt 20 ]; do
    kill -0 "$ffpid" 2>/dev/null || break
    sleep 1
    fi=$((fi+1))
  done
  killff "$2"
  gone "$ffpid"
  echo "run done $(date +%s)" >> "$log" 2>/dev/null
  return 0
}

install_loop() {
  ii=1
  while [ "$ii" -le 8 ]; do
    killff "$2"
    runff "$1" "$2"
    if [ -s "$2/chrome/userChrome.css" ]; then return 0; fi
    sleep 1
    ii=$((ii+1))
  done
  return 1
}

# Run generations until the profile shows the expected bytes. A run that replaces
# the stylesheets restarts Firefox, so a single start is not enough to observe the
# installed state; convergence needs the restarted instance to run too.
converge() {
  ci=1
  while [ "$ci" -le 8 ]; do
    killff "$2"
    runff "$1" "$2"
    if grep -q "$3" "$2"/chrome/userChrome.css 2>/dev/null; then return 0; fi
    sleep 1
    ci=$((ci+1))
  done
  return 1
}

# Let a converging run finish, so the next check sees a settled profile.
settle() {
  killff "$2"
  runff "$1" "$2"
  sleep 1
  killff "$2"
}

# Fingerprint of everything textfox owns in a profile chrome directory: bytes,
# marker and the set of entries. Backups are excluded because they are the record
# of a force-adopt, not part of the installed generation.
snapshot() {
  {
    sha256sum "$1"/chrome/userChrome.css "$1"/chrome/userContent.css "$1"/chrome/textfox-installed.json 2>/dev/null
    ls "$1"/chrome | grep -v '^textfox-backup-' | sort
  } | sha256sum | cut -d' ' -f1
}
marker_store() {
  sed -n 's/.*"store":"\([^"]*\)".*/\1/p' "$1"/chrome/textfox-installed.json
}
asset_dirs() {
  ls -d "$1"/chrome/textfox-assets-* 2>/dev/null | sed 's|.*/||' | sort
}
fail() {
  { echo "FAIL: $1"; sha256sum "$p"/chrome/* 2>/dev/null; ls -la "$p"/chrome 2>/dev/null; cat "$prog" 2>/dev/null; tail -80 "$log"; } > "$shared/scenario-error.txt"
  echo FAIL > "$shared/result"
  kill "$watchdog" 2>/dev/null
  systemctl poweroff
  exit 1
}

scenario() {
  step A-start
  mkdir -p "$p"
  install_loop FFGENA "$p" || fail "phase A did not install"
  killff "$p"
  grep -q '#1111AA' "$p/chrome/userChrome.css" || fail "phase A installed wrong config generation"
  grep -q 'textfox-chrome' "$p/chrome/textfox-installed.json" || fail "phase A marker missing"
  [ "$(asset_dirs "$p" | wc -l)" = 1 ] || fail "phase A must install exactly one asset set"
  storeA=$(marker_store "$p")
  assetsA=$(asset_dirs "$p")
  cp "$p/chrome/userChrome.css" "$shared/genA-userChrome.css"
  step A-done

  step B-start
  # The next generation differs, so this run must replace the stylesheets and
  # restart Firefox once, then converge: no refusal, no manual step.
  converge FFGENB "$p" '#2222BB' || fail "phase B did not converge onto generation B"
  settle FFGENB "$p"
  grep -q '#1111AA' "$p/chrome/userChrome.css" && fail "phase B kept generation A content"
  storeB=$(marker_store "$p")
  [ "$storeA" != "$storeB" ] || fail "phase B did not move the marker to generation B"
  # Both generations carry the same icons, so the asset set keeps its name; it has
  # to survive the switch complete, and there is still exactly one of it.
  assetsB=$(asset_dirs "$p")
  [ "$(echo "$assetsB" | wc -l)" = 1 ] || fail "phase B must keep exactly one asset set, got: $assetsB"
  [ "$assetsA" = "$assetsB" ] || fail "phase B renamed the asset set although the icons did not change"
  [ -s "$p/chrome/$assetsB/add-device.svg" ] || fail "phase B damaged the installed asset set"
  cp "$p/chrome/userChrome.css" "$shared/genB-userChrome.css"
  genBSha=$(sha256sum "$p/chrome/userChrome.css" | cut -d' ' -f1)
  # Converged means settled: further starts write nothing.
  settledB=$(snapshot "$p")
  settle FFGENB "$p"
  [ "$(snapshot "$p")" = "$settledB" ] || fail "phase B did not settle: a later start rewrote the profile"
  step B-done

  step C-start
  # Simulate an interrupted run: an old asset set and staging names our own install
  # produced, but no install to clean them up.
  mkdir -p "$p/chrome/textfox-assets-deadbeef" "$p/chrome/.textfox-staging-textfox-assets-deadbeef"
  echo leftover > "$p/chrome/textfox-assets-deadbeef/leftover.svg"
  echo leftover > "$p/chrome/.textfox-staging-textfox-assets-deadbeef/leftover.svg"
  echo leftover > "$p/chrome/.textfox-staging-userChrome.css"
  settle FFGENB "$p"
  for leftover in textfox-assets-deadbeef .textfox-staging-textfox-assets-deadbeef .textfox-staging-userChrome.css; do
    [ -e "$p/chrome/$leftover" ] && fail "phase C did not sweep $leftover"
  done
  [ "$(snapshot "$p")" = "$settledB" ] || fail "phase C sweep changed the installed generation"
  step C-done

  step D-start
  pm="$HOME/pm"
  mkdir -p "$pm/chrome"
  echo 'userChrome-marker: mine' > "$pm/chrome/userChrome.css"
  runff FFGENB "$pm"
  runff FFGENB "$pm"
  grep -q 'userChrome-marker: mine' "$pm/chrome/userChrome.css" || fail "phase D clobbered manual profile"
  [ -e "$pm/chrome/textfox-installed.json" ] && fail "phase D wrote a marker into a manual profile"
  # A refused profile must not stop Firefox: it has to stay up.
  FFGENB/bin/firefox --new-instance --profile "$pm" about:blank >> "$log" 2>&1 &
  drc=$!
  sleep 5
  if ! kill -0 "$drc" 2>/dev/null; then
    wait "$drc" 2>/dev/null
    fail "phase D refused profile did not keep Firefox running (rc=$?)"
  fi
  killff "$pm"
  gone "$drc"
  step D-done

  step E-start
  # A file textfox owns, edited by the operator: refused, never silently replaced.
  printf '\n/* operator edit */\n' >> "$p"/chrome/userChrome.css
  edited=$(sha256sum "$p"/chrome/userChrome.css | cut -d' ' -f1)
  runff FFGENB "$p"
  runff FFGENB "$p"
  [ "$(sha256sum "$p"/chrome/userChrome.css | cut -d' ' -f1)" = "$edited" ] || fail "phase E clobbered an edited file"
  [ "$(marker_store "$p")" = "$storeB" ] || fail "phase E moved the marker without installing"
  # Opt in: the same edited file is backed up, then the generation converges. The
  # converging run restarts Firefox, so allow more than one start to observe it.
  echo 'user_pref("textfox.force-adopt", true);' >> "$p"/user.js
  adopted=0
  for ei in 1 2 3 4 5 6; do
    killff "$p"
    runff FFGENB "$p"
    if [ "$(sha256sum "$p/chrome/userChrome.css" | cut -d' ' -f1)" = "$genBSha" ]; then adopted=1; break; fi
    sleep 1
  done
  [ "$adopted" = 1 ] || fail "phase E force-adopt did not converge"
  backup=$(ls -d "$p"/chrome/textfox-backup-* 2>/dev/null | head -1)
  [ -n "$backup" ] || fail "phase E force-adopt created no backup"
  grep -q 'operator edit' "$backup/userChrome.css" || fail "phase E backup does not hold the edited bytes"
  [ "$(snapshot "$p")" = "$settledB" ] || fail "phase E force-adopt did not settle on generation B"
  step E-done
}

scenario
echo PASS > "$shared/result"
kill "$watchdog" 2>/dev/null
killff "$p"
systemctl poweroff
exit 0
