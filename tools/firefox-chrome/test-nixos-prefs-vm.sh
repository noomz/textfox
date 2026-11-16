#!/bin/sh
# Boots check-nixos-prefs-vm.nix: the NixOS module migrating an existing profile.
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
run="$root/tools/firefox-chrome/nix-portable-run.sh"
work=$(mktemp -d "${TMPDIR:-/tmp}/textfox-prefs-vm.XXXXXX")
mkdir "$work/shared"
cp "$root/chrome/defaults.css" "$work/shared/defaults.css"
echo "VM artifacts: $work"
vm=$(sh "$run" nix build --offline --no-link --print-out-paths --impure --expr "import $root/tools/firefox-chrome/check-nixos-prefs-vm.nix { nixpkgsPath = $nixpkgs; }")
set +e
NIX_DISK_IMAGE="$work/disk.qcow2" SHARED_DIR="$work/shared" QEMU_OPTS='-nographic' \
  timeout 240 sh "$run" "$vm" run-nixos-vm >"$work/boot.log" 2>&1
status=$?
set -e
if [ "$status" -eq 0 ] && [ -f "$work/shared/result" ] && [ "$(cat "$work/shared/result")" = PASS ]; then
  echo 'NixOS preference migration VM passed'
  python3 -m json.tool "$work/shared/preferences.json"
  python3 -m json.tool "$work/shared/persisted.json"
else
  echo "NixOS preference migration VM failed (exit $status); see $work/boot.log and $work/shared/firefox.log" >&2
  for artifact in failure probe-error.txt persisted.json; do
    [ -f "$work/shared/$artifact" ] || continue
    printf '%s: ' "$artifact" >&2
    cat "$work/shared/$artifact" >&2
    echo >&2
  done
  exit 1
fi
