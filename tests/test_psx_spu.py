import numpy as np

from src.psx.spu import SPU, decode_block, envelope_step

ON, OFF = 0xC000, 0x0000  # SPUCNT: enabled + unmuted


def _block(shift: int, filt: int, nibbles: list[int], flags: int = 0) -> bytes:
    data = bytearray([(filt << 4) | shift, flags])
    for i in range(0, 28, 2):
        data.append((nibbles[i] & 0xF) | (nibbles[i + 1] & 0xF) << 4)
    return bytes(data)


def _silent_loop() -> bytes:
    return _block(0, 0, [0] * 28, flags=0x07)  # loop start + end + repeat


def _spu_with_sample(blocks: bytes, addr: int = 0x1000) -> SPU:
    spu = SPU()
    spu.write_ram(addr, blocks)
    spu.write_reg(0x1AA, ON)
    spu.write_reg(0x180, 0x3FFF)  # main volume L/R
    spu.write_reg(0x182, 0x3FFF)
    spu.write_reg(0x000, 0x3FFF)  # voice 0 volume L/R
    spu.write_reg(0x002, 0x3FFF)
    spu.write_reg(0x004, 0x1000)  # pitch 44.1kHz
    spu.write_reg(0x006, addr // 8)
    spu.write_reg(0x008, 0x000F)  # fastest attack, sustain level max
    spu.write_reg(0x00A, 0x0000)
    return spu


def test_decode_block_applies_shift_and_filter():
    samples, s1, s2 = decode_block(_block(12, 0, [1, -1] + [0] * 26), 0, 0)

    assert samples[:3].tolist() == [1, -1, 0]
    assert (s1, s2) == (0, 0)


def test_decode_block_uses_prediction_from_history():
    samples, _, _ = decode_block(_block(12, 1, [0] * 28), 64, 0)

    # filter 1: s = s_prev * 60 / 64
    assert samples[0] == (64 * 60 + 32) // 64
    assert samples[1] == (samples[0] * 60 + 32) // 64


def test_decode_block_clamps_to_16_bit():
    samples, _, _ = decode_block(_block(0, 1, [7] * 28), 0x7FFF, 0x7FFF)

    assert samples.max() == 0x7FFF


def test_linear_attack_reaches_max_and_moves_to_decay():
    level, counter = 0, 0
    steps = 0
    while level < 0x7FFF:
        level, counter = envelope_step(level, counter, exp=False, dec=False, shift=0, step=0)
        steps += 1

    assert steps == 3  # +7<<11 per tick, saturating at 7FFFh
    assert level == 0x7FFF


def test_slow_envelope_only_steps_when_counter_overflows():
    level, counter = 0, 0
    levels = []
    for _ in range(8):
        level, counter = envelope_step(level, counter, exp=False, dec=False, shift=13, step=0)
        levels.append(level)

    # shift 13 => counter increment 2000h, one +7 step every 4 ticks
    assert levels == [0, 0, 0, 7, 7, 7, 7, 14]


def test_exponential_decrease_scales_step_by_level():
    level, _ = envelope_step(0x4000, 0, exp=True, dec=True, shift=0, step=0)

    assert level == 0x4000 + ((-8 << 11) * 0x4000 >> 15)


def test_keyed_voice_produces_sound_and_silent_spu_is_zero():
    dc = _block(0, 0, [4] * 28) * 3 + _silent_loop()
    spu = _spu_with_sample(dc)

    assert not spu.render(64).any()

    spu.write_reg(0x188, 1)
    out = spu.render(64)
    assert out[:, 0].max() > 0
    assert out.dtype == np.int16 and out.shape == (64, 2)


def test_end_mute_flag_silences_the_voice():
    one_shot = _block(0, 0, [4] * 28, flags=0x01)  # end without repeat -> mute
    spu = _spu_with_sample(one_shot + _silent_loop())
    spu.write_reg(0x188, 1)

    out = spu.render(200)
    assert out[10:28, 0].max() > 0
    assert not out[40:, 0].any()


def test_key_off_releases_to_silence():
    loop = _block(0, 0, [4] * 28, flags=0x07)
    spu = _spu_with_sample(loop)
    spu.write_reg(0x00A, 0x0000)  # release shift 0: fastest
    spu.write_reg(0x188, 1)
    spu.render(100)

    spu.write_reg(0x18C, 1)
    out = spu.render(100)
    assert not out[20:].any()


def test_mute_bit_silences_dry_output():
    spu = _spu_with_sample(_block(0, 0, [4] * 28) * 3 + _silent_loop())
    spu.write_reg(0x1AA, 0x8000)  # enabled but muted
    spu.write_reg(0x188, 1)

    assert not spu.render(64).any()


def test_reverb_keeps_sound_after_the_voice_stops():
    spu = _spu_with_sample(_block(0, 0, [7] * 28, flags=0x01) + _silent_loop())
    spu.write_reg(0x1A2, 0xE000)  # reverb work area
    spu.write_reg(0x184, 0x4000)  # reverb output volume
    spu.write_reg(0x186, 0x4000)
    regs = [0x0010, 0x0008, 0x4000, 0x4000, 0, 0, 0, 0,
            0x4000, 0x4000, 0x0200, 0x0200, 0x0100, 0x0100, 0, 0,
            0x0100, 0x0100, 0x0200, 0x0200, 0, 0, 0, 0,
            0x0100, 0x0100, 0x0080, 0x0080, 0x0040, 0x0040, 0x4000, 0x4000]
    for i, v in enumerate(regs):
        spu.write_reg(0x1C0 + i * 2, v)
    spu.write_reg(0x198, 1)  # voice 0 to reverb
    spu.write_reg(0x1AA, ON | 0x80)
    spu.write_reg(0x188, 1)

    out = spu.render(4000)
    assert out[2000:, 0].any()
