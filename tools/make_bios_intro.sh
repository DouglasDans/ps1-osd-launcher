#!/usr/bin/env bash
# Records the Sony intro of a PS1 BIOS into assets/bios_intro.trace and
# pre-renders its jingle into assets/bios_intro.wav.
# usage: tools/make_bios_intro.sh <bios.bin>
#
# CD_SPINUP (seconds) is how long the empty drive keeps trying to read before
# the BIOS gives up and cuts to its menu; 4.8 matches the DuckStation
# recording (the Sony screen holds ~9.5s after it is complete).
set -euo pipefail

bios="${1:?usage: $0 <bios.bin>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
python="${PYTHON:-$root/.venv/bin/python}"
[ -x "$python" ] || python=python3

make -s -C "$root/tools/psxtrace"
CD_SPINUP="${CD_SPINUP:-4.8}" "$root/tools/psxtrace/psxtrace" "$bios" "$root/assets/bios_intro.trace" \
    2> >(grep -v '^\[post' >&2)
(cd "$root" && "$python" -m src.psx.replay assets/bios_intro.trace assets/bios_intro.wav)
echo "intro salva em $root/assets/bios_intro.{trace,wav}"
