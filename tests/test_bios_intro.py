import os
import struct
import time
import wave

import pygame
import pytest

from src import bios_intro
from src.psx.trace import CPU_HZ, MAGIC

FIELD = CPU_HZ // 60


@pytest.fixture
def screen(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    pygame.display.init()
    yield pygame.display.set_mode((320, 180))
    pygame.mixer.quit()
    pygame.display.quit()


def _write_trace(path, fields: int) -> str:
    red_screen = [
        ("g", 0x03000000), ("g", 0x08000027), ("g", 0x06C60260), ("g", 0x0703FC10),
        ("G", 0xE3000000), ("G", 0xE4000000 | (511 << 10) | 1023), ("G", 0xE5000000),
        ("G", 0x020000F8), ("G", 0), ("G", (480 << 16) | 640),
    ]
    out = bytearray(MAGIC)
    for i in range(fields):
        for tag, word in red_screen:
            out += tag.encode() + struct.pack("<I", word)
        out += b"V" + struct.pack("<Q", FIELD * (i + 1))
    path.write_bytes(bytes(out))
    return str(path)


def test_missing_trace_reports_failure_so_the_caller_can_fall_back(screen, tmp_path):
    assert bios_intro.play(screen, str(tmp_path / "none.trace"), str(tmp_path / "none.wav")) is False


def test_invalid_trace_reports_failure(screen, tmp_path):
    bad = tmp_path / "bad.trace"
    bad.write_bytes(b"not a trace")

    assert bios_intro.play(screen, str(bad), str(tmp_path / "none.wav")) is False


def test_intro_runs_until_the_last_recorded_field_then_returns(screen, tmp_path):
    trace = _write_trace(tmp_path / "intro.trace", fields=12)

    t0 = time.perf_counter()
    assert bios_intro.play(screen, trace, str(tmp_path / "none.wav")) is True
    elapsed = time.perf_counter() - t0

    assert 12 / 60 - 0.02 <= elapsed < 12 / 60 + 0.5
    assert screen.get_at((160, 90))[:3] == (0xF8, 0, 0)  # true color: the fill color as sent


def test_audio_is_left_playing_for_the_tail_over_the_menu(screen, tmp_path):
    trace = _write_trace(tmp_path / "intro.trace", fields=3)
    wav = tmp_path / "intro.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x10" * 2 * 44100)  # 1s, longer than the 3 fields

    assert bios_intro.play(screen, trace, str(wav)) is True

    assert pygame.mixer.get_init()
    assert pygame.mixer.get_busy()


def test_missing_audio_only_silences_the_intro(screen, tmp_path):
    trace = _write_trace(tmp_path / "intro.trace", fields=2)

    assert bios_intro.play(screen, trace, os.path.join(tmp_path, "missing.wav")) is True


def test_background_is_held_after_the_cut(screen, tmp_path):
    trace = _write_trace(tmp_path / "intro.trace", fields=3)
    background = tmp_path / "purple.png"
    surf = pygame.Surface((8, 8))
    surf.fill((0x40, 0x30, 0xC0))
    pygame.image.save(surf, str(background))

    t0 = time.perf_counter()
    bios_intro.play(screen, trace, str(tmp_path / "none.wav"),
                    background=str(background), background_seconds=0.3)

    assert time.perf_counter() - t0 >= 3 / 60 + 0.3
    assert screen.get_at((160, 90))[:3] == (0x40, 0x30, 0xC0)


def test_stop_audio_silences_and_releases_the_device(screen, tmp_path):
    trace = _write_trace(tmp_path / "intro.trace", fields=3)
    wav = tmp_path / "intro.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x10" * 2 * 44100)
    bios_intro.play(screen, trace, str(wav))

    bios_intro.stop_audio()

    assert not pygame.mixer.get_init()
