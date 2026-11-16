#!/bin/sh
# One entry point for nix-portable work, so a sandbox policy can allow this
# script once instead of every `nix-portable` shape a test needs.
#
#   nix-portable-run.sh nix <nix arguments...>
#       Run the nix CLI (build, eval, store cat) inside nix-portable's store.
#   nix-portable-run.sh shell <store-path> <command> [arguments...]
#       Run a command in a nix shell built from that store path.
#   nix-portable-run.sh <store-path> <command> [arguments...]
#       Same as `shell`, for the common case.
#
# NP_RUNTIME defaults to bwrap; export NP_RUNTIME=proot to override.
set -eu

nix_portable="${NP:-${HOME}/.local/bin/nix-portable}"
runtime="${NP_RUNTIME:-bwrap}"

if [ "$#" -lt 1 ]; then
    echo "usage: $0 nix <arguments...> | $0 [shell] <store-path> <command> [arguments...]" >&2
    exit 2
fi

case "$1" in
nix | nix-build | nix-store | nix-shell | nix-env | nix-instantiate)
    exec env NP_RUNTIME="$runtime" "$nix_portable" "$@"
    ;;
shell)
    [ "$#" -ge 3 ] || { echo "usage: $0 shell <store-path> <command> [arguments...]" >&2; exit 2; }
    store="$2"
    shift 2
    ;;
*)
    [ "$#" -ge 2 ] || { echo "usage: $0 <store-path> <command> [arguments...]" >&2; exit 2; }
    store="$1"
    shift
    ;;
esac

exec env NP_RUNTIME="$runtime" "$nix_portable" nix shell --offline "$store" -c "$@"
