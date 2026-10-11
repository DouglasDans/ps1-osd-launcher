import struct

import pytest

from src.psx.trace import RamWrite, RegWrite, parse


def _trace(*records: bytes) -> bytes:
    return b"PSXTRC01" + b"".join(records)


def test_gpu_words_are_grouped_by_the_vblank_that_ends_each_field():
    data = _trace(
        b"G" + struct.pack("<I", 0xE1000000),
        b"g" + struct.pack("<I", 0x03000000),
        b"V" + struct.pack("<Q", 1000),
        b"G" + struct.pack("<I", 0x01000000),
        b"V" + struct.pack("<Q", 2000),
    )

    t = parse(data)

    assert [f.end_cycle for f in t.fields] == [1000, 2000]
    assert t.fields[0].gpu == [(0, 0xE1000000), (1, 0x03000000)]
    assert t.fields[1].gpu == [(0, 0x01000000)]


def test_spu_register_and_ram_writes_keep_their_cycle_order():
    data = _trace(
        b"S" + struct.pack("<QHH", 10, 0x1AA, 0x8000),
        b"R" + struct.pack("<QII", 20, 0x1000, 3) + b"\x01\x02\x03",
        b"V" + struct.pack("<Q", 30),
    )

    t = parse(data)

    assert t.spu == [RegWrite(10, 0x1AA, 0x8000), RamWrite(20, 0x1000, b"\x01\x02\x03")]


def test_commands_after_the_last_vblank_are_dropped():
    data = _trace(
        b"V" + struct.pack("<Q", 5),
        b"G" + struct.pack("<I", 0x28000000),
    )

    assert len(parse(data).fields) == 1


def test_rejects_files_without_the_trace_header():
    with pytest.raises(ValueError):
        parse(b"NOTATRACE")
