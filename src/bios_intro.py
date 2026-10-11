"""Sony intro recreated from the PS1 BIOS.

Replays assets/bios_intro.trace (recorded from the BIOS by tools/psxtrace):
the GPU commands are drawn field by field, against the wall clock, the way an
upscaling emulator would (output resolution, progressive, true color;
textures replaced by HD versions when available, JINC2-filtered otherwise).
They are drawn by the graphics card (src.psx.gpurender) when SDL can open a
renderer on the display, or on the CPU (src.psx.hires) otherwise. The jingle
is synthesized ahead of time from the same trace by src.psx.spu
(assets/bios_intro.wav) and played as a single sound.

The trace ends where the BIOS cuts from the Sony screen to its menu. Its
purple background (without the menu) can be held for a while after the cut,
with the end of the jingle still sounding; the caller stops the audio with
stop_audio() when its own menu shows up. The display is left as a plain
surface again (pygame.display.get_surface()), showing that background.
"""

import logging
import os
import time

import pygame
from pygame._sdl2 import video

from src.psx import replay, textures
from src.psx.gpurender import RendererGPU, close_renderer, display_flags, open_renderer
from src.psx.hires import HiResGPU
from src.psx.spu import SAMPLE_RATE
from src.psx.trace import load

log = logging.getLogger("ps1.bios_intro")

# the Sony screen is 640x480 (interlaced on the console)
_NATIVE_W, _NATIVE_H = 640, 480


def play(screen: pygame.Surface, trace_path: str, audio_path: str,
         resolution: tuple[int, int] | None = None, texture_filter: str = "jinc2",
         background: str | None = None, background_seconds: float = 0.0,
         textures_dir: str | None = None, use_gpu: bool = True) -> bool:
    """Plays the intro up to the cut to the BIOS menu, then shows background
    for background_seconds. Returns False when the trace isn't available, so
    the caller can fall back to something else.

    textures_dir holds HD texture replacements (src.psx.textures).
    resolution is the internal rendering resolution of the CPU renderer
    (default: the screen's); a lower one is scaled up to the screen."""
    if not os.path.exists(trace_path):
        log.warning("Trace da intro não encontrado: %s", trace_path)
        return False
    try:
        trace = load(trace_path)
    except (OSError, ValueError):
        log.exception("Trace da intro inválido: %s", trace_path)
        return False

    size, flags = screen.get_size(), display_flags(screen)
    hd = textures.load(textures_dir) if textures_dir else {}
    renderer = open_renderer(screen) if use_gpu else None
    if renderer is not None:
        w, h = size
        gpu = RendererGPU(renderer, size, texture_filter=texture_filter, replacements=hd)
    else:
        w, h = resolution or size
        gpu = HiResGPU(scale=(w / _NATIVE_W, h / _NATIVE_H), texture_filter=texture_filter, replacements=hd)
    t0 = time.perf_counter()
    gpu.prepare(trace)
    log.info("Reproduzindo intro da BIOS: %d fields, %dx%d na %s, %d texturas HD, filtro %s (preparo %.2fs)",
             len(trace.fields), w, h, "GPU" if renderer else "CPU", len(hd), texture_filter,
             time.perf_counter() - t0)
    sound = _load_sound(audio_path)
    if sound:
        sound.play()

    if renderer is not None:
        _run(trace, gpu, gpu.show)
        image = _load_background(background, size) if background else None
        if image is not None:
            bg = video.Texture.from_surface(renderer, image)
            renderer.target = None
            renderer.blit(bg, pygame.Rect((0, 0), size))
            renderer.present()
            _wait(background_seconds)
        del gpu, renderer
        screen = close_renderer(size, flags)
        if image is not None:  # keep the background up while the menu loads
            screen.blit(image, (0, 0))
            pygame.display.flip()
    else:
        _run(trace, gpu, lambda frame: _blit(screen, frame))
        image = _load_background(background, size) if background else None
        if image is not None:
            screen.blit(image, (0, 0))
            pygame.display.flip()
            _wait(background_seconds)
    log.info("Intro da BIOS encerrada")
    return True


def stop_audio() -> None:
    """Silences the jingle and frees the audio device for the apps."""
    if pygame.mixer.get_init():
        pygame.mixer.quit()


def _load_background(path: str, size: tuple[int, int]) -> pygame.Surface | None:
    try:
        return pygame.transform.smoothscale(pygame.image.load(path), size)
    except (pygame.error, FileNotFoundError):
        log.warning("Fundo da BIOS não encontrado: %s", path)
        return None


def _wait(seconds: float) -> None:
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


def _blit(screen: pygame.Surface, image) -> None:
    h, w = image.shape[:2]
    surf = pygame.image.frombuffer(image, (w, h), "RGB")
    if (w, h) != screen.get_size():
        surf = pygame.transform.smoothscale(surf, screen.get_size())
    screen.blit(surf, (0, 0))
    pygame.display.flip()


def _run(trace, gpu, show) -> None:
    """Replays the fields against the wall clock; show(frame) puts a changed
    picture on screen."""
    rate = replay.field_rate(trace)
    total = len(trace.fields)
    behind = [0]  # fields before this one won't be shown: draw only the latest
    frames = replay.video(trace, gpu=gpu, skip=lambda i: i < behind[0])

    shown, image, drawn = -1, None, None
    flips, worst_late = 0, 0.0
    t0 = time.perf_counter()
    while True:
        pygame.event.get()
        elapsed = time.perf_counter() - t0

        target = min(int(elapsed * rate), total - 1)
        behind[0] = target
        while shown < target:
            image = next(frames)
            shown += 1

        if image is not drawn:
            show(image)
            drawn = image
            flips += 1
            worst_late = max(worst_late, time.perf_counter() - (t0 + (shown + 1) / rate))

        if elapsed >= total / rate:
            # late = drawn after the next field was due (frames were skipped)
            log.info("Intro: %d quadros desenhados, pior atraso %.0f ms", flips, worst_late * 1000)
            return
        next_field = t0 + (shown + 1) / rate
        time.sleep(max(0.0, min(next_field - time.perf_counter(), 0.01)))
