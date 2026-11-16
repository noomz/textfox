#!/bin/sh
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
# The Home Manager source must come from the branch that pairs with the nixpkgs
# source: the fixtures ask for `home.stateVersion = "26.05"`, and a release
# branch whose enum stops earlier refuses to evaluate. With nixpkgs-unstable use
# home-manager `master`.
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/textfox-home-manager-vm.XXXXXX")
mkdir "$work/shared"
cp "$root/chrome/defaults.css" "$work/shared/defaults.css"
echo "VM artifacts: $work"
runner="$root/tools/firefox-chrome/nix-portable-run.sh"
vm=$(sh "$runner" nix build --offline --no-link --print-out-paths --impure --expr "import $root/tools/firefox-chrome/check-home-manager-vm.nix { nixpkgsPath = $1; homeManagerPath = $2; }")
set +e
NIX_DISK_IMAGE="$work/disk.qcow2" SHARED_DIR="$work/shared" QEMU_OPTS='-nographic' \
  timeout 200 sh "$runner" "$vm" run-nixos-vm >"$work/boot.log" 2>&1
status=$?
set -e
if [ "$status" -eq 0 ] && [ -f "$work/shared/result" ] && [ "$(cat "$work/shared/result")" = PASS ]; then
  echo 'Home Manager activation VM passed'
  python3 -m json.tool "$work/shared/preferences.json"
  echo 'prefs.js after Firefox shut down:'
  cat "$work/shared/persisted-prefs.js"
else
  echo "Home Manager activation VM failed (exit $status); see $work/boot.log" >&2
  if [ -f "$work/shared/probe-error.txt" ]; then
    python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text(), file=sys.stderr)' "$work/shared/probe-error.txt"
  fi
  exit 1
fi
