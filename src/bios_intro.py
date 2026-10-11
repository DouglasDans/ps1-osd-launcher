"""Sony intro recreated from the PS1 BIOS.

Replays assets/bios_intro.trace (recorded from the BIOS by tools/psxtrace):
the GPU commands are rasterized by src.psx.gpu field by field, against the
wall clock. The jingle is synthesized ahead of time from the same trace by
src.psx.spu (assets/bios_intro.wav) and played as a single sound.

The trace ends where the BIOS cuts from the Sony screen to its menu. The
jingle's reverb tail outlives that cut by a fraction of a second, so the
mixer is left playing; whoever needs the audio device next releases it.
"""

import logging
import os
import time

import pygame

from src.psx import replay
from src.psx.spu import SAMPLE_RATE
from src.psx.trace import load

log = logging.getLogger("ps1.bios_intro")


def play(screen: pygame.Surface, trace_path: str, audio_path: str) -> bool:
    """Plays the intro up to the cut to the BIOS menu. Returns False when the
    trace isn't available, so the caller can fall back to something else."""
    if not os.path.exists(trace_path):
        log.warning("Trace da intro não encontrado: %s", trace_path)
        return False
    try:
        trace = load(trace_path)
    except (OSError, ValueError):
        log.exception("Trace da intro inválido: %s", trace_path)
        return False

    log.info("Reproduzindo intro da BIOS: %d fields", len(trace.fields))
    sound = _load_sound(audio_path)
    if sound:
        sound.play()
    _run(screen, trace)
    log.info("Intro da BIOS encerrada")
    return True


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


def _run(screen: pygame.Surface, trace) -> None:
    rate = replay.field_rate(trace)
    total = len(trace.fields)
    frames = replay.video(trace)
    size = screen.get_size()

    shown, image, drawn = -1, None, None
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
            surf = pygame.image.frombuffer(image.tobytes(), (w, h), "RGB")
            screen.blit(pygame.transform.scale(surf, size), (0, 0))
            pygame.display.flip()
            drawn = image

        if elapsed >= total / rate:
            return
        next_field = t0 + (shown + 1) / rate
        time.sleep(max(0.0, min(next_field - time.perf_counter(), 0.01)))
