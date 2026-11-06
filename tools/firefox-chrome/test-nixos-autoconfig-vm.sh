#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /nix/store/<nixpkgs-source>" >&2
  exit 2
fi
nixpkgs=$1
case "$nixpkgs" in
  /nix/store/*) ;;
  *) echo 'Expected a nixpkgs source path in /nix/store' >&2; exit 2 ;;
esac
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/textfox-autoconfig-vm.XXXXXX")
mkdir "$work/shared"
echo "VM artifacts: $work"
vm=$(NP_RUNTIME=bwrap nix-portable nix build --no-link --print-out-paths --impure --expr "import $root/tools/firefox-chrome/check-nixos-autoconfig-vm.nix { nixpkgsPath = $nixpkgs; }")
set +e
NP_RUNTIME=bwrap NIX_DISK_IMAGE="$work/disk.qcow2" SHARED_DIR="$work/shared" QEMU_OPTS='-nographic' \
  timeout 150 nix-portable nix shell --offline "$vm" --command run-nixos-vm >"$work/boot.log" 2>&1
status=$?
set -e
if [ "$status" -eq 0 ] && [ -f "$work/shared/result" ] && [ "$(cat "$work/shared/result")" = PASS ]; then
  echo 'NixOS autoconfig VM passed'
  python3 -m json.tool "$work/shared/preferences.json"
  python3 -m json.tool "$work/shared/vanilla-preferences.json"
else
  echo "NixOS autoconfig VM failed (exit $status); see $work/boot.log" >&2
  if [ -f "$work/shared/probe-error.txt" ]; then
    python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text(), file=sys.stderr)' "$work/shared/probe-error.txt"
  fi
  exit 1
fi
