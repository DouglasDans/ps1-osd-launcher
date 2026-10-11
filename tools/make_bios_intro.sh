#!/usr/bin/env bash
# Records the Sony intro of a PS1 BIOS into assets/bios_intro.trace and
# pre-renders its jingle into assets/bios_intro.wav.
# usage: tools/make_bios_intro.sh <bios.bin>
#
# With REALESRGAN pointing to a realesrgan-ncnn-vulkan binary (its models/
# folder next to it), the logo textures are also upscaled into
# assets/bios_textures/ (HD replacements; JINC2 filtering is used without them).
#
# CD_SPINUP (seconds) is how long the empty drive keeps trying to read before
# the BIOS gives up and cuts to its menu. 4.8 matches the DuckStation
# recording (the Sony screen holds ~9.5s after it is complete); 9.8 holds it
# 5s longer, covering more of the Pi's boot.
set -euo pipefail

bios="${1:?usage: $0 <bios.bin>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
python="${PYTHON:-$root/.venv/bin/python}"
export PYGAME_HIDE_SUPPORT_PROMPT=1
[ -x "$python" ] || python=python3

make -s -C "$root/tools/psxtrace"
CD_SPINUP="${CD_SPINUP:-9.8}" "$root/tools/psxtrace/psxtrace" "$bios" "$root/assets/bios_intro.trace" \
    2> >(grep -v '^\[post' >&2)
(cd "$root" && "$python" -m src.psx.replay assets/bios_intro.trace assets/bios_intro.wav)
echo "intro salva em $root/assets/bios_intro.{trace,wav}"

if [ -n "${REALESRGAN:-}" ]; then
    rm -rf "$root/assets/bios_textures"
    (cd "$root" && "$python" -m src.psx.textures assets/bios_intro.trace assets/bios_textures "$REALESRGAN" \
        2> >(grep -v '^\[' >&2))
    echo "texturas HD salvas em $root/assets/bios_textures"
fi
