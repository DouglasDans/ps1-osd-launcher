"""Replays a psxtrace recording through the GPU and SPU models."""

from collections.abc import Iterator

import numpy as np

from src.psx.gpu import GPU
from src.psx.spu import CYCLES_PER_SAMPLE, SAMPLE_RATE, SPU
from src.psx.trace import CPU_HZ, RegWrite, Trace


def field_rate(trace: Trace) -> float:
    """Fields per second, measured from the recorded vblank timestamps."""
    if len(trace.fields) < 2:
        return 59.94
    span = trace.fields[-1].end_cycle - trace.fields[0].end_cycle
    return (len(trace.fields) - 1) * CPU_HZ / span


def video(trace: Trace, gpu: GPU | None = None) -> Iterator[np.ndarray]:
    """Yields the displayed RGB image (h, w, 3) at the end of every field.

    When a field repeats the commands of the two previous ones and those left
    VRAM untouched, the scene reached a fixed point (both interlace fields are
    drawn): the field is skipped and the previous image object is yielded again.
    """
    gpu = gpu or GPU()
    history: list[tuple[list, bool]] = []  # (commands, vram changed) of recent fields
    image = None
    for i, f in enumerate(trace.fields):
        gpu.field = i & 1
        if len(history) >= 2 and all(cmds == f.gpu and not changed for cmds, changed in history[-2:]):
            history = history[-2:] + [(f.gpu, False)]
            yield image
            continue

        before = gpu.vram.copy()
        for port, word in f.gpu:
            if port:
                gpu.gp1(word)
            else:
                gpu.gp0(word)
        changed = not np.array_equal(before, gpu.vram)
        history = history[-2:] + [(f.gpu, changed)]
        if changed or image is None or any(port for port, _ in f.gpu):
            image = gpu.display_rgb()
        yield image


def audio_chunks(trace: Trace, chunk: int = 4096, tail_seconds: float = 3.0,
                 spu: SPU | None = None) -> Iterator[np.ndarray]:
    """Renders the jingle progressively (int16 stereo at 44.1kHz from power-on),
    applying each recorded SPU write at the sample where it happened."""
    spu = spu or SPU()
    end_cycle = trace.fields[-1].end_cycle if trace.fields else 0
    total = end_cycle // CYCLES_PER_SAMPLE + int(tail_seconds * SAMPLE_RATE)
    events = trace.spu
    e = 0
    for start in range(0, total, chunk):
        stop = min(start + chunk, total)
        parts = []
        pos = start
        while e < len(events) and events[e].cycle // CYCLES_PER_SAMPLE < stop:
            ev = events[e]
            at = max(ev.cycle // CYCLES_PER_SAMPLE, pos)
            if at > pos:
                parts.append(spu.render(at - pos))
                pos = at
            if isinstance(ev, RegWrite):
                spu.write_reg(ev.reg, ev.value)
            else:
                spu.write_ram(ev.addr, ev.data)
            e += 1
        if stop > pos:
            parts.append(spu.render(stop - pos))
        yield np.concatenate(parts) if len(parts) > 1 else parts[0]


def audio(trace: Trace, tail_seconds: float = 3.0, spu: SPU | None = None) -> np.ndarray:
    """The whole jingle as one int16 stereo array."""
    chunks = list(audio_chunks(trace, tail_seconds=tail_seconds, spu=spu))
    return np.concatenate(chunks) if chunks else np.zeros((0, 2), dtype=np.int16)
