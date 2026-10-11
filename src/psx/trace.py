"""Reader for the trace produced by tools/psxtrace (see its header for the format)."""

import struct
from dataclasses import dataclass, field

MAGIC = b"PSXTRC01"
CPU_HZ = 33_868_800


@dataclass
class Field:
    end_cycle: int
    gpu: list[tuple[int, int]] = field(default_factory=list)  # (port 0=GP0/1=GP1, word)


@dataclass(frozen=True)
class RegWrite:
    cycle: int
    reg: int
    value: int


@dataclass(frozen=True)
class RamWrite:
    cycle: int
    addr: int
    data: bytes


@dataclass
class Trace:
    fields: list[Field]
    spu: list[RegWrite | RamWrite]


def parse(data: bytes) -> Trace:
    if not data.startswith(MAGIC):
        raise ValueError("not a psxtrace file")

    fields: list[Field] = []
    spu: list[RegWrite | RamWrite] = []
    pending: list[tuple[int, int]] = []
    unpack = struct.unpack_from
    i, n = len(MAGIC), len(data)
    while i < n:
        tag = data[i]
        i += 1
        if tag == 0x47:  # 'G'
            pending.append((0, unpack("<I", data, i)[0]))
            i += 4
        elif tag == 0x67:  # 'g'
            pending.append((1, unpack("<I", data, i)[0]))
            i += 4
        elif tag == 0x56:  # 'V'
            fields.append(Field(unpack("<Q", data, i)[0], pending))
            pending = []
            i += 8
        elif tag == 0x53:  # 'S'
            spu.append(RegWrite(*unpack("<QHH", data, i)))
            i += 12
        elif tag == 0x52:  # 'R'
            cycle, addr, size = unpack("<QII", data, i)
            i += 16
            spu.append(RamWrite(cycle, addr, data[i:i + size]))
            i += size
        else:
            raise ValueError(f"unknown record {tag:#x} at offset {i - 1}")
    return Trace(fields, spu)


def load(path: str) -> Trace:
    with open(path, "rb") as f:
        return parse(f.read())
