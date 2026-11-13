#!/bin/sh
# Run Home Manager's activate script for the textfox module on a disposable
# home. This host is not NixOS. Run it on Linux and on Darwin; each run
# proves the host it is on.
#
# The activation script writes $HOME and, on Linux, reloads a user systemd
# session when that bus is up. Every XDG directory stays inside the disposable
# home, and the Nix profile directory is created there so setupVars does not
# fall through to the real per-user profile.
#
# On Linux, Nix is nix-portable with NP_RUNTIME=bwrap. On Darwin, Nix is the
# host install and Firefox is $FIREFOX or `firefox` on PATH.
set -eu
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 /nix/store/<nixpkgs-source> /nix/store/<home-manager-source>" >&2
  exit 2
fi
for path in "$@"; do
  case "$path" in
    /nix/store/*) ;;
    *) echo 'Expected source paths in /nix/store' >&2; exit 2 ;;
  esac
done

os=$(uname -s)
if [ "$os" != Darwin ]; then
  export NP_RUNTIME=bwrap
fi
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
runner="$root/tools/firefox-chrome/nix-portable-run.sh"
probe="$root/tools/firefox-chrome/probe-vm-preferences.py"
user=$(id -un)
nixpkgs=$1
home_manager=$2
work=$(mktemp -d "${TMPDIR:-/tmp}/textfox-hm-standalone.XXXXXX")
real_home=$HOME
if [ "$os" = Darwin ]; then
  store_root=
else
  store_root=$real_home/.nix-portable
fi
nix_bin=
ff_pid=
echo "Standalone artifacts: $work"

# The activation script takes Nix from `type -p nix-env`. On the Linux host
# that command is the proot wrapper, so the store's own Nix has to come first.
load_nix() {
  if [ "$os" != Darwin ]; then
    return
  fi
  if [ -f /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ]; then
    set +u
    . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
    set -u
  fi
}

ensure_nix_bin() {
  gen=$1
  if [ -n "$nix_bin" ]; then
    return
  fi
  if [ "$os" = Darwin ]; then
    load_nix
    found=$(command -v nix-env || true)
    test -n "$found" || fail 'nix-env is not on PATH'
    nix_bin=$(dirname "$found")
    return
  fi
  found=$(sh "$runner" "$gen" bash -c 'set -- /nix/store/*-nix-2.20.6/bin/nix-env; echo "$1"')
  nix_bin=$(dirname "$found")
  test -n "$nix_bin" || fail 'could not find the store Nix 2.20.6'
}

profile_dir() {
  home=$1
  if [ "$os" = Darwin ]; then
    printf '%s\n' "$home/Library/Application Support/Firefox/Profiles/test"
  else
    printf '%s\n' "$home/.config/mozilla/firefox/test"
  fi
}

run_bash() {
  gen=$1
  script=$2
  if [ "$os" = Darwin ]; then
    bash -c "$script"
  else
    sh "$runner" "$gen" bash -c "$script"
  fi
}

# Home Manager links at /nix/store. This host keeps that store under
# ~/.nix-portable, so walk the links before reading them from outside bwrap.
resolve() {
  path=$1
  i=0
  while [ -L "$path" ]; do
    target=$(readlink "$path")
    case "$target" in
      /nix/store/*) path=$store_root$target ;;
      /*) path=$target ;;
      *) path=$(dirname "$path")/$target ;;
    esac
    i=$((i + 1))
    if [ "$i" -gt 10 ]; then
      fail "symlink loop resolving $1"
    fi
  done
  printf '%s\n' "$path"
}

fail() {
  echo "$*" >&2
  exit 1
}

stamp() {
  if [ ! -e "$1" ]; then
    echo missing
    return
  fi
  if [ "$os" = Darwin ]; then
    stat -f %m "$1"
  else
    stat -c %Y "$1"
  fi
}

profiles_stamp=$(stamp "$real_home/.local/state/nix/profiles")
nix_profile_stamp=$(stamp "$real_home/.nix-profile")
mozilla_stamp=$(stamp "$real_home/.config/mozilla")
if [ "$os" = Darwin ]; then
  firefox_support_stamp=$(stamp "$real_home/Library/Application Support/Firefox")
  per_user_profile_stamp=$(stamp "/nix/var/nix/profiles/per-user/$user")
fi

assert_real_home_untouched() {
  test "$(stamp "$real_home/.local/state/nix/profiles")" = "$profiles_stamp" ||
    fail 'activation changed the real Nix profile directory'
  test "$(stamp "$real_home/.nix-profile")" = "$nix_profile_stamp" ||
    fail 'activation changed the real ~/.nix-profile link'
  test "$(stamp "$real_home/.config/mozilla")" = "$mozilla_stamp" ||
    fail 'activation changed the real Firefox config directory'
  if [ "$os" = Darwin ]; then
    test "$(stamp "$real_home/Library/Application Support/Firefox")" = "$firefox_support_stamp" ||
      fail 'activation changed the real Firefox support directory'
    test "$(stamp "/nix/var/nix/profiles/per-user/$user")" = "$per_user_profile_stamp" ||
      fail 'activation changed the real per-user Nix profile'
  fi
}

prepare_home() {
  home=$1
  mkdir -p \
    "$home/.local/state/nix/profiles" \
    "$home/.local/state/home-manager/gcroots" \
    "$home/.config" \
    "$home/.local/share" \
    "$home/.cache" \
    "$home/run"
}

build_generation() {
  home=$1
  load_nix
  expr="import $root/tools/firefox-chrome/check-home-manager-standalone.nix {
    nixpkgsPath = $nixpkgs;
    homeManagerPath = $home_manager;
    homeDirectory = \"$home\";
    username = \"$user\";
  }"
  if [ "$os" = Darwin ]; then
    nix --extra-experimental-features nix-command build \
      --no-link --print-out-paths --impure --expr "$expr"
  else
    sh "$runner" nix build --offline --no-link --print-out-paths --impure --expr "$expr"
  fi
}

activate_env() {
  home=$1
  prefix=${2:-$nix_bin}
  printf '%s\n' \
    "set -eu" \
    "export HOME='$home'" \
    "export PATH='$prefix':\"\$PATH\"" \
    "export XDG_STATE_HOME='$home/.local/state'" \
    "export XDG_CONFIG_HOME='$home/.config'" \
    "export XDG_DATA_HOME='$home/.local/share'" \
    "export XDG_CACHE_HOME='$home/.cache'" \
    "export XDG_RUNTIME_DIR='$home/run'"
}

run_activate() {
  home=$1
  gen=$2
  log=$3
  ensure_nix_bin "$gen"
  prepare_home "$home"
  run_bash "$gen" "$(activate_env "$home")
    exec '$gen/activate'
  " >"$log" 2>&1
}

child_pids() {
  if [ "$os" = Darwin ]; then
    pgrep -P "$1" || true
  else
    ps -o pid= --ppid "$1" 2>/dev/null || true
  fi
}

kill_tree() {
  pid=$1
  signal=$2
  for child in $(child_pids "$pid"); do
    kill_tree "$child" "$signal"
  done
  kill "-$signal" "$pid" 2>/dev/null || true
}

stop_pid() {
  pid=$1
  kill_tree "$pid" TERM
  i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 40 ]; then
      kill_tree "$pid" KILL
      break
    fi
    sleep 0.5
  done
  wait "$pid" 2>/dev/null || true
}

# A warm store finishes activation before a poll can catch it. Hold nix-env
# --set, which is the profile write, and kill the run there.
interrupt_activate() {
  home=$1
  gen=$2
  log=$3
  ensure_nix_bin "$gen"
  prepare_home "$home"
  wrap=$work/nix-wrap
  mkdir -p "$wrap"
  # nix_bin is a /nix/store path. The binaries are on the host under the
  # nix-portable store, and the symlinks must point back at /nix/store so
  # they resolve inside bwrap.
  for tool in "$store_root$nix_bin"/*; do
    name=$(basename "$tool")
    if [ "$name" = nix-env ]; then
      cat >"$wrap/nix-env" <<EOF
#!/bin/sh
if [ "\${1:-}" = --profile ] && [ "\${3:-}" = --set ]; then
  touch '$home/nix-env-entered'
  while [ -e '$home/hold-nix-env' ]; do
    sleep 0.2
  done
fi
exec '$nix_bin/nix-env' "\$@"
EOF
      chmod +x "$wrap/nix-env"
    else
      ln -s "$nix_bin/$name" "$wrap/$name"
    fi
  done
  touch "$home/hold-nix-env"
  run_bash "$gen" "$(activate_env "$home" "$wrap")
    exec '$gen/activate'
  " >"$log" 2>&1 &
  pid=$!
  i=0
  while [ ! -e "$home/nix-env-entered" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo '--- interrupt log ---' >&2
      cat "$log" >&2
      fail 'activation finished before the interrupt'
    fi
    i=$((i + 1))
    if [ "$i" -gt 600 ]; then
      stop_pid "$pid"
      rm -f "$home/hold-nix-env"
      echo '--- interrupt log ---' >&2
      cat "$log" >&2
      fail 'timed out waiting to interrupt activation'
    fi
    sleep 0.2
  done
  stop_pid "$pid"
  rm -f "$home/hold-nix-env"
}

assert_skipped_systemd() {
  log=$1
  if grep -q 'Attempting to reload services' "$log"; then
    fail 'activation tried to reload the user systemd session'
  fi
  # systemd.user is off on Darwin, so the skip line is a Linux activation fact.
  if [ "$os" != Darwin ]; then
    grep -q 'User systemd daemon not running. Skipping reload.' "$log" ||
      fail "activation did not skip the systemd reload ($log)"
  fi
}

assert_installed() {
  home=$1
  profile=$(profile_dir "$home")
  config_css=$(resolve "$profile/chrome/config.css")
  user_js=$(resolve "$profile/user.js")
  user_chrome=$(resolve "$profile/chrome/userChrome.css")
  icon=$(resolve "$profile/chrome/icons/blank.svg")
  if [ "$os" = Darwin ]; then
    test -d "$home/Library/Application Support/Firefox/Profiles/test" ||
      fail 'Darwin activation did not write Library/Application Support/Firefox/Profiles/test'
    test ! -d "$home/Library/Application Support/Firefox/Profiles/Profiles" ||
      fail 'Darwin activation doubled Profiles/'
  else
    test ! -d "$home/Library" || fail 'Linux activation wrote a Darwin Library path'
  fi
  test -f "$icon" || fail "icons were not linked in $profile"
  test -f "$config_css" || fail "config.css was not linked in $profile"
  grep -q -- '--tf-border-width: 7px;' "$config_css" ||
    fail 'config.css does not set the configured border'
  grep -q -- '--tf-font-size: 17px;' "$config_css" ||
    fail 'config.css does not set the configured font size'
  grep -q -- '--tf-display-vertical-tabs: none;' "$config_css" ||
    fail 'config.css does not hide vertical tabs'
  grep -q -- '--tf-display-titles: none;' "$config_css" ||
    fail 'config.css does not hide titles'
  grep -qxF 'user_pref("browser.nova.enabled", false);' "$user_js" ||
    fail 'user.js does not disable browser Nova'
  grep -qxF 'user_pref("widget.macos.native-context-menus", false);' "$user_js" ||
    fail 'user.js does not keep macOS context menus in Firefox'
  python3 - "$user_chrome" <<'PY' || fail 'userChrome.css does not import config.css after defaults.css'
import pathlib, sys
imports = [line for line in pathlib.Path(sys.argv[1]).read_text().splitlines() if line.startswith("@import")]
expected = ['@import url("defaults.css");', '@import url("config.css");']
if imports[-2:] != expected:
    raise SystemExit(imports)
PY
}

assert_notes() {
  profile=$1
  test "$(cat "$profile/chrome/user-notes.txt")" = 'kept by the user' ||
    fail 'activation removed the user file in chrome/'
  grep -qxF 'user_pref("tf.test.sentinel", true);' "$profile/prefs.js" ||
    fail 'activation removed the sentinel preference'
}

firefox_check() {
  gen=$1
  profile=$2
  output=$3
  shift 3
  firefox_bin=${FIREFOX:-firefox}
  command -v "$firefox_bin" >/dev/null || test -x "$firefox_bin" || fail 'firefox is not on PATH'
  if [ "$os" = Darwin ]; then
    profile=$(CDPATH= cd -- "$profile" && pwd -P)
  else
    profile=$(CDPATH= cd -- "$profile" && pwd)
  fi
  port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
  mkdir -p "$(dirname "$output")" /tmp/shared
  printf 'user_pref("marionette.port", %s);\n' "$port" >>"$profile/prefs.js"
  # Activation links stylesheets into /nix/store. On Linux that store is
  # visible inside bwrap, so Firefox runs there. On Darwin the host store is /nix.
  extra=
  if [ "$#" -gt 0 ]; then
    extra=$(printf '%q ' "$@")
  fi
  set +e
  run_bash "$gen" "
    set -eu
    unset LD_LIBRARY_PATH NIX_LDFLAGS
    export MOZ_HEADLESS=1 MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1
    '$firefox_bin' --marionette -remote-allow-system-access --new-instance \
      --profile '$profile' about:blank >/tmp/shared/firefox-standalone.log 2>&1 &
    ff=\$!
    python3 '$probe' \
      --profile '$profile' \
      --port '$port' \
      --output '$output' \
      --border 7px \
      --defaults-css '$work/defaults.css' \
      --expect-vars '{\"--tf-font-size\":\"17px\",\"--tf-border-width\":\"7px\",\"--tf-display-vertical-tabs\":\"none\",\"--tf-display-titles\":\"none\"}' \
      --expect-prefs '{\"shyfox.enable.context.menu.icons\":true,\"shyfox.enable.ext.mono.toolbar.icons\":false}' \
      $extra
    status=\$?
    kill -TERM \$ff 2>/dev/null || true
    i=0
    while kill -0 \$ff 2>/dev/null; do
      i=\$((i + 1))
      if [ \$i -gt 40 ]; then
        kill -KILL \$ff 2>/dev/null || true
        break
      fi
      sleep 0.5
    done
    wait \$ff 2>/dev/null || true
    exit \$status
  "
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    echo '--- firefox log ---' >&2
    cat /tmp/shared/firefox-standalone.log >&2 || true
    if [ -f /tmp/shared/probe-error.txt ]; then
      cat /tmp/shared/probe-error.txt >&2
    fi
    fail "Firefox did not show the activated profile ($profile)"
  fi
}

assert_newtab_default() {
  python3 - "$1" <<'PY' || fail 'new-tab Nova was given a user value'
import json, sys
prefs = json.load(open(sys.argv[1]))["preferences"]
nova = prefs["browser.nova.enabled"]
newtab = prefs["browser.newtabpage.activity-stream.nova.enabled"]
if nova["effective"] is not False or nova["user"] is not True:
    raise SystemExit(f"browser Nova {nova}")
if newtab["user"] is not False or newtab["effective"] is not True:
    raise SystemExit(f"new-tab Nova {newtab}")
PY
}

cp "$root/chrome/defaults.css" "$work/defaults.css"

echo 'Seeded home: interrupt, then two completed activations'
seeded=$work/seeded
seeded_profile=$(profile_dir "$seeded")
mkdir -p "$seeded_profile/chrome"
printf '%s\n' \
  'user_pref("browser.nova.enabled", true);' \
  'user_pref("browser.shell.checkDefaultBrowser", false);' \
  'user_pref("tf.test.sentinel", true);' >"$seeded_profile/prefs.js"
printf 'kept by the user' >"$seeded_profile/chrome/user-notes.txt"
seeded_gen=$(build_generation "$seeded")
echo "Seeded generation: $seeded_gen"
interrupt_activate "$seeded" "$seeded_gen" "$work/seeded-interrupt.log"
assert_notes "$seeded_profile"
run_activate "$seeded" "$seeded_gen" "$work/seeded-activate.log" || {
  cat "$work/seeded-activate.log" >&2
  fail 'activation after the interrupt failed'
}
assert_skipped_systemd "$work/seeded-activate.log"
assert_installed "$seeded"
assert_notes "$seeded_profile"
run_activate "$seeded" "$seeded_gen" "$work/seeded-again.log" || {
  cat "$work/seeded-again.log" >&2
  fail 'second activation failed'
}
assert_skipped_systemd "$work/seeded-again.log"
assert_installed "$seeded"
assert_notes "$seeded_profile"
firefox_check "$seeded_gen" "$seeded_profile" "$work/seeded.json" \
  --preserve-pref 'tf.test.sentinel=true' \
  --preserve-pref 'browser.shell.checkDefaultBrowser=false' \
  --preserve-file "$seeded_profile/chrome/user-notes.txt=kept by the user"
assert_newtab_default "$work/seeded.json"
grep -qxF 'user_pref("tf.test.sentinel", true);' "$seeded_profile/prefs.js" ||
  fail 'prefs.js lost the sentinel after Firefox shut down'
grep -qxF 'user_pref("browser.nova.enabled", false);' "$seeded_profile/prefs.js" ||
  fail 'prefs.js did not persist Nova off after Firefox shut down'
assert_notes "$seeded_profile"

echo 'Fresh home: one activation'
fresh=$work/fresh
mkdir -p "$fresh"
fresh_gen=$(build_generation "$fresh")
echo "Fresh generation: $fresh_gen"
run_activate "$fresh" "$fresh_gen" "$work/fresh-activate.log" || {
  cat "$work/fresh-activate.log" >&2
  fail 'fresh activation failed'
}
assert_skipped_systemd "$work/fresh-activate.log"
assert_installed "$fresh"
fresh_profile=$(profile_dir "$fresh")
test ! -e "$fresh_profile/chrome/user-notes.txt" ||
  fail 'fresh profile gained the seeded user file'
firefox_check "$fresh_gen" "$fresh_profile" "$work/fresh.json"
assert_newtab_default "$work/fresh.json"
grep -qxF 'user_pref("browser.nova.enabled", false);' "$fresh_profile/prefs.js" ||
  fail 'fresh prefs.js did not persist Nova off after Firefox shut down'
if grep -q 'tf.test.sentinel' "$fresh_profile/prefs.js"; then
  fail 'fresh prefs.js contains the seeded sentinel'
fi

assert_real_home_untouched
echo 'Standalone home-manager activation passed'
python3 -m json.tool "$work/seeded.json"
python3 -m json.tool "$work/fresh.json"
