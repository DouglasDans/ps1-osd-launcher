"""Sony intro recreated from the PS1 BIOS.

Replays assets/bios_intro.trace (recorded from the BIOS by tools/psxtrace):
the GPU commands are drawn field by field, against the wall clock, by
src.psx.hires the way an upscaling emulator would (output resolution,
progressive, true color; textures replaced by HD versions when available,
JINC2-filtered otherwise). The jingle is synthesized ahead of time from the same trace by
src.psx.spu (assets/bios_intro.wav) and played as a single sound.

The trace ends where the BIOS cuts from the Sony screen to its menu. Its
purple background (without the menu) can be held for a while after the cut,
with the end of the jingle still sounding; the caller stops the audio with
stop_audio() when its own menu shows up.
"""

import logging
import os
import time

import pygame

from src.psx import replay, textures
from src.psx.hires import HiResGPU
from src.psx.spu import SAMPLE_RATE
from src.psx.trace import load

log = logging.getLogger("ps1.bios_intro")

# the Sony screen is 640x480 (interlaced on the console)
_NATIVE_W, _NATIVE_H = 640, 480


def play(screen: pygame.Surface, trace_path: str, audio_path: str,
         resolution: tuple[int, int] | None = None, texture_filter: str = "jinc2",
         background: str | None = None, background_seconds: float = 0.0,
         textures_dir: str | None = None) -> bool:
    """Plays the intro up to the cut to the BIOS menu, then shows background
    for background_seconds. Returns False when the trace isn't available, so
    the caller can fall back to something else.

    resolution is the internal rendering resolution (default: the screen's);
    a lower one is scaled up to the screen. textures_dir holds HD texture
    replacements (src.psx.textures)."""
    if not os.path.exists(trace_path):
        log.warning("Trace da intro não encontrado: %s", trace_path)
        return False
    try:
        trace = load(trace_path)
    except (OSError, ValueError):
        log.exception("Trace da intro inválido: %s", trace_path)
        return False

    w, h = resolution or screen.get_size()
    hd = textures.load(textures_dir) if textures_dir else {}
    gpu = HiResGPU(scale=(w / _NATIVE_W, h / _NATIVE_H), texture_filter=texture_filter, replacements=hd)
    t0 = time.perf_counter()
    gpu.prepare(trace)
    log.info("Reproduzindo intro da BIOS: %d fields, %dx%d, %d texturas HD, filtro %s (preparo %.2fs)",
             len(trace.fields), w, h, len(hd), texture_filter, time.perf_counter() - t0)
    sound = _load_sound(audio_path)
    if sound:
        sound.play()
    _run(screen, trace, gpu)
    if background:
        _hold(screen, background, background_seconds)
    log.info("Intro da BIOS encerrada")
    return True


def stop_audio() -> None:
    """Silences the jingle and frees the audio device for the apps."""
    if pygame.mixer.get_init():
        pygame.mixer.quit()


def _hold(screen: pygame.Surface, path: str, seconds: float) -> None:
    try:
        img = pygame.image.load(path).convert()
    except (pygame.error, FileNotFoundError):
        log.warning("Fundo da BIOS não encontrado: %s", path)
        return
    screen.blit(pygame.transform.smoothscale(img, screen.get_size()), (0, 0))
    pygame.display.flip()
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pygame.event.get()
        time.sleep(min(0.01, max(0.0, end - time.perf_counter())))


def _load_sound(path: str) -> pygame.mixer.Sound | None:
    if not os.path.exists(path):
        log.warning("Áudio da intro não encontrado: %s — intro sem som", path)
        return None
    try:
        pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=1024)
        return pygame.mixer.Sound(path)
    except pygame.error:
        log.exception("Mixer indisponível — intro sem som")
        return None


def _run(screen: pygame.Surface, trace, gpu: HiResGPU) -> None:
    rate = replay.field_rate(trace)
    total = len(trace.fields)
    frames = replay.video(trace, gpu=gpu)
    size = screen.get_size()

    shown, image, drawn = -1, None, None
    flips, worst_late = 0, 0.0
    t0 = time.perf_counter()
    while True:
        pygame.event.get()
        elapsed = time.perf_counter() - t0

        target = min(int(elapsed * rate), total - 1)
        while shown < target:
            image = next(frames)
            shown += 1

        if image is not drawn:
            h, w = image.shape[:2]
            surf = pygame.image.frombuffer(image, (w, h), "RGB")
            if (w, h) != size:
                surf = pygame.transform.smoothscale(surf, size)
            screen.blit(surf, (0, 0))
            pygame.display.flip()
            drawn = image
            flips += 1
            worst_late = max(worst_late, time.perf_counter() - (t0 + (shown + 1) / rate))

        if elapsed >= total / rate:
            # late = drawn after the next field was due (frames were skipped)
            log.info("Intro: %d quadros desenhados, pior atraso %.0f ms", flips, worst_late * 1000)
            return
        next_field = t0 + (shown + 1) / rate
        time.sleep(max(0.0, min(next_field - time.perf_counter(), 0.01)))
