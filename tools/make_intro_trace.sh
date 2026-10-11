#!/usr/bin/env bash
# Records the Sony intro of a PS1 BIOS into assets/bios_intro.trace.
# usage: tools/make_intro_trace.sh <bios.bin>
set -euo pipefail

bios="${1:?usage: $0 <bios.bin>}"
root="$(cd "$(dirname "$0")/.." && pwd)"

make -s -C "$root/tools/psxtrace"
"$root/tools/psxtrace/psxtrace" "$bios" "$root/assets/bios_intro.trace" 2> >(grep -v '^\[post' >&2)
echo "trace salvo em $root/assets/bios_intro.trace"
