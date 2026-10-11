import numpy as np

from src.psx.replay import audio, audio_chunks, field_rate, video
from src.psx.spu import CYCLES_PER_SAMPLE, SAMPLE_RATE
from src.psx.trace import CPU_HZ, Field, RamWrite, RegWrite, Trace

FIELD = 565_000  # ~59.94 fields per second, in CPU cycles


def _display_setup():
    return [(1, 0x03000000), (1, 0x08000027), (1, 0x06C60260), (1, 0x0703FC10),
            (0, 0xE3000000), (0, 0xE4000000 | (511 << 10) | 1023), (0, 0xE5000000)]


def _flat_screen(color: int):
    quad = [(0, 0x28000000 | color), (0, 0), (0, 640), (0, 480 << 16), (0, (480 << 16) | 640)]
    return _display_setup() + quad


def test_field_rate_comes_from_vblank_timestamps():
    t = Trace([Field(FIELD * (i + 1)) for i in range(4)], [])

    assert abs(field_rate(t) - CPU_HZ / FIELD) < 1e-9


def test_video_yields_one_image_per_field_with_the_drawing():
    t = Trace([Field(FIELD, _flat_screen(0x0000F8)), Field(2 * FIELD, _flat_screen(0x0000F8))], [])

    images = list(video(t))

    assert len(images) == 2
    # interlaced 480i: each field only draws its own half of the lines
    assert images[0][1, 0].tolist() == [0xFF, 0, 0]
    assert images[1][0, 0].tolist() == [0xFF, 0, 0]


def test_video_reuses_the_image_once_the_scene_is_static():
    fields = [Field(FIELD * (i + 1), _flat_screen(0x00F800)) for i in range(6)]

    images = list(video(Trace(fields, [])))

    assert images[-1] is images[-2]
    assert images[-1][0, 0].tolist() == [0, 0xFF, 0]


def _tone_trace(key_on_cycle: int) -> Trace:
    block = bytes([0x00, 0x03]) + bytes([0x44] * 14)  # loud DC, loop end + repeat
    events = [
        RamWrite(0, 0x1000, block),
        RegWrite(0, 0x1AA, 0xC000),
        RegWrite(0, 0x180, 0x3FFF), RegWrite(0, 0x182, 0x3FFF),
        RegWrite(0, 0x000, 0x3FFF), RegWrite(0, 0x002, 0x3FFF),
        RegWrite(0, 0x004, 0x1000), RegWrite(0, 0x006, 0x1000 // 8),
        RegWrite(0, 0x008, 0x000F), RegWrite(0, 0x00A, 0x0000),
        RegWrite(key_on_cycle, 0x188, 1),
    ]
    return Trace([Field(10 * FIELD)], events)


def test_audio_covers_the_trace_plus_tail():
    pcm = audio(_tone_trace(FIELD), tail_seconds=0.5)

    assert pcm.shape == (10 * FIELD // CYCLES_PER_SAMPLE + SAMPLE_RATE // 2, 2)


def test_audio_starts_at_the_recorded_key_on_time():
    key_on = 3 * FIELD
    pcm = audio(_tone_trace(key_on), tail_seconds=0.1)

    first = int(np.argmax(np.abs(pcm[:, 0]) > 0))
    assert abs(first - key_on // CYCLES_PER_SAMPLE) < 8


def test_chunked_audio_matches_single_render():
    t = _tone_trace(2 * FIELD)

    whole = audio(t, tail_seconds=0.2)
    pieces = np.concatenate(list(audio_chunks(_tone_trace(2 * FIELD), chunk=1000, tail_seconds=0.2)))

    assert np.array_equal(whole, pieces)
