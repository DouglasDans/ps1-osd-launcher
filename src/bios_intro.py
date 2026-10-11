"""Sony intro recreated from the PS1 BIOS.

Replays assets/bios_intro.trace (recorded from the BIOS by tools/psxtrace):
the GPU commands are rasterized by src.psx.gpu field by field, and the SPU
writes drive src.psx.spu, which synthesizes the jingle. The audio is rendered
in a separate process (the Pi has spare cores) and streamed to the mixer while
the main process draws the video against the wall clock.
"""

import logging
import multiprocessing
import os
import queue
import time
from collections.abc import Callable

import pygame

from src.psx import replay
from src.psx.spu import SAMPLE_RATE
from src.psx.trace import load

log = logging.getLogger("ps1.bios_intro")

_NOT_READY = object()
# the jingle only starts ~2.8s after power-on; trimming silence before that is expected
_LATE_WARN_AFTER = 2 * SAMPLE_RATE


class AudioFeeder:
    """Keeps a mixer channel fed with chunks (int16 stereo bytes) that are
    aligned to the intro timeline: chunk k starts at the sum of the previous
    chunk lengths. When the channel runs dry (start, or the renderer fell
    behind), the next chunk is trimmed or dropped so sound stays in sync."""

    def __init__(self, channel, source: Callable[[], object], sound: Callable[[bytes], object]):
        self._channel = channel
        self._source = source
        self._sound = sound
        self._next = 0  # timeline sample where the next chunk starts
        self.finished = False

    def pump(self, now_sample: int) -> None:
        while not self.finished and (not self._channel.get_busy()
                                     or self._channel.get_queue() is None):
            data = self._source()
            if data is _NOT_READY:
                return
            if data is None:
                self.finished = True
                return
            start, n = self._next, len(data) // 4
            self._next += n
            if self._channel.get_busy():
                self._channel.queue(self._sound(data))
                continue
            late = now_sample - start
            if late > 0 and now_sample > _LATE_WARN_AFTER:
                log.warning("Áudio da intro atrasado %.0f ms — realinhando", late * 1000 / SAMPLE_RATE)
            if late >= n:
                continue
            if late > 0:
                data = data[late * 4:]
            self._channel.play(self._sound(data))


def _render_audio(trace_path: str, out) -> None:
    """Worker process: synthesizes the jingle and streams it chunk by chunk."""
    try:
        for chunk in replay.audio_chunks(load(trace_path)):
            out.put(chunk.tobytes())
    finally:
        out.put(None)


class _AudioStream:
    def __init__(self, trace_path: str) -> None:
        self._proc = None
        self.feeder = None
        try:
            pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=1024)
        except pygame.error:
            log.warning("Mixer indisponível — intro sem som")
            return

        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")  # the worker re-imports pygame
        ctx = multiprocessing.get_context("spawn")
        self._queue = ctx.Queue()
        self._proc = ctx.Process(target=_render_audio, args=(trace_path, self._queue), daemon=True)
        self._proc.start()
        self.feeder = AudioFeeder(pygame.mixer.Channel(0), self._next_chunk,
                                  sound=lambda b: pygame.mixer.Sound(buffer=b))

    def _next_chunk(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return _NOT_READY

    def pump(self, now_sample: int) -> None:
        if self.feeder:
            self.feeder.pump(now_sample)

    def close(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc.join(timeout=1)
        if pygame.mixer.get_init():
            pygame.mixer.quit()


def play(screen: pygame.Surface, trace_path: str, hold_seconds: float = 0.0) -> bool:
    """Plays the intro. Returns False when the trace isn't available, so the
    caller can fall back to something else."""
    if not os.path.exists(trace_path):
        log.warning("Trace da intro não encontrado: %s", trace_path)
        return False
    try:
        trace = load(trace_path)
    except (OSError, ValueError):
        log.exception("Trace da intro inválido: %s", trace_path)
        return False

    log.info("Reproduzindo intro da BIOS: %d fields", len(trace.fields))
    audio = _AudioStream(trace_path)
    try:
        _run(screen, trace, audio, hold_seconds)
    finally:
        audio.close()
    log.info("Intro da BIOS encerrada")
    return True


def _run(screen: pygame.Surface, trace, audio: _AudioStream, hold_seconds: float) -> None:
    rate = replay.field_rate(trace)
    total = len(trace.fields)
    end = total / rate + hold_seconds
    frames = replay.video(trace)
    size = screen.get_size()

    shown, image, drawn = -1, None, None
    t0 = time.perf_counter()
    while True:
        pygame.event.get()
        elapsed = time.perf_counter() - t0
        audio.pump(int(elapsed * SAMPLE_RATE))

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

        if elapsed >= end:
            return
        next_field = t0 + (shown + 1) / rate
        time.sleep(max(0.0, min(next_field - time.perf_counter(), 0.01)))
