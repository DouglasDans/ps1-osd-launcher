"""Software model of the PS1 SPU (Sound Processing Unit).

Covers what the BIOS boot jingle uses: 24 ADPCM voices with 4-point
gaussian interpolation, ADSR envelopes, direct voice/main volumes, and the
reverb unit (with its 39-tap FIR resampling between 44.1kHz and 22.05kHz).
Formulas follow psx-spx ("Sound Processing Unit"). Register offsets are
relative to 1F801C00h, exactly as recorded by tools/psxtrace.

Sound RAM content comes from write_ram(): the trace records every upload
(manual FIFO writes and DMA) as RAM writes, so the FIFO data register
(1A8h) itself is ignored here.
"""

import numpy as np

SAMPLE_RATE = 44100
CYCLES_PER_SAMPLE = 768  # 33.8688MHz / 44100Hz

GAUSS = (
    -1, -1, -1, -1, -1, -1, -1, -1,
    -1, -1, -1, -1, -1, -1, -1, -1,
    0, 0, 0, 0, 0, 0, 0, 1,
    1, 1, 1, 2, 2, 2, 3, 3,
    3, 4, 4, 5, 5, 6, 7, 7,
    8, 9, 9, 10, 11, 12, 13, 14,
    15, 16, 17, 18, 19, 21, 22, 24,
    25, 27, 28, 30, 32, 33, 35, 37,
    39, 41, 44, 46, 48, 51, 53, 56,
    58, 61, 64, 67, 70, 73, 77, 80,
    84, 87, 91, 95, 99, 103, 107, 111,
    116, 120, 125, 130, 135, 140, 145, 150,
    156, 161, 167, 173, 179, 186, 192, 199,
    205, 212, 219, 227, 234, 242, 250, 257,
    266, 274, 283, 291, 300, 309, 319, 328,
    338, 348, 358, 369, 379, 390, 401, 412,
    424, 436, 448, 460, 473, 485, 498, 512,
    525, 539, 553, 567, 582, 597, 612, 627,
    643, 659, 675, 692, 708, 726, 743, 761,
    779, 797, 816, 835, 854, 874, 894, 914,
    935, 956, 977, 999, 1020, 1043, 1066, 1089,
    1112, 1136, 1160, 1184, 1209, 1234, 1260, 1286,
    1312, 1339, 1366, 1394, 1422, 1450, 1479, 1508,
    1537, 1567, 1598, 1628, 1660, 1691, 1723, 1756,
    1789, 1822, 1856, 1890, 1924, 1959, 1995, 2031,
    2067, 2104, 2141, 2179, 2217, 2256, 2295, 2334,
    2374, 2415, 2456, 2497, 2539, 2582, 2624, 2668,
    2712, 2756, 2801, 2846, 2892, 2938, 2985, 3032,
    3079, 3128, 3176, 3225, 3275, 3325, 3376, 3427,
    3479, 3531, 3584, 3637, 3691, 3745, 3799, 3855,
    3910, 3967, 4023, 4081, 4138, 4197, 4255, 4315,
    4374, 4435, 4495, 4557, 4619, 4681, 4744, 4807,
    4871, 4935, 5000, 5065, 5131, 5197, 5264, 5332,
    5399, 5468, 5536, 5606, 5676, 5746, 5817, 5888,
    5959, 6032, 6104, 6177, 6251, 6325, 6400, 6475,
    6550, 6626, 6702, 6779, 6856, 6934, 7012, 7091,
    7170, 7249, 7329, 7409, 7490, 7571, 7653, 7735,
    7817, 7900, 7983, 8066, 8150, 8234, 8319, 8404,
    8489, 8575, 8661, 8748, 8834, 8922, 9009, 9097,
    9185, 9273, 9362, 9451, 9541, 9630, 9720, 9811,
    9901, 9992, 10083, 10174, 10266, 10358, 10450, 10542,
    10635, 10727, 10820, 10913, 11007, 11100, 11194, 11288,
    11382, 11476, 11571, 11665, 11760, 11855, 11950, 12045,
    12140, 12236, 12331, 12427, 12522, 12618, 12714, 12809,
    12905, 13001, 13097, 13193, 13289, 13385, 13481, 13577,
    13673, 13769, 13865, 13961, 14056, 14152, 14248, 14343,
    14439, 14534, 14630, 14725, 14820, 14915, 15010, 15104,
    15199, 15293, 15387, 15481, 15575, 15669, 15762, 15855,
    15948, 16041, 16133, 16226, 16317, 16409, 16500, 16592,
    16682, 16773, 16863, 16953, 17042, 17131, 17220, 17308,
    17396, 17484, 17571, 17658, 17744, 17830, 17916, 18001,
    18086, 18170, 18254, 18337, 18420, 18502, 18584, 18665,
    18746, 18826, 18905, 18985, 19063, 19141, 19219, 19295,
    19372, 19447, 19522, 19597, 19671, 19744, 19816, 19888,
    19959, 20030, 20100, 20169, 20238, 20306, 20373, 20439,
    20505, 20570, 20634, 20698, 20760, 20822, 20884, 20944,
    21004, 21063, 21121, 21178, 21235, 21290, 21345, 21399,
    21452, 21505, 21556, 21607, 21657, 21706, 21754, 21801,
    21848, 21893, 21938, 21982, 22025, 22066, 22107, 22148,
    22187, 22225, 22262, 22299, 22334, 22369, 22402, 22435,
    22467, 22498, 22527, 22556, 22584, 22611, 22637, 22662,
    22686, 22709, 22731, 22752, 22772, 22791, 22809, 22826,
    22842, 22857, 22872, 22885, 22897, 22908, 22918, 22927,
    22935, 22942, 22948, 22953, 22957, 22960, 22962, 22963,
)

FIR = (
    -0x0001, 0x0000, 0x0002, 0x0000, -0x000A, 0x0000, 0x0023, 0x0000,
    -0x0067, 0x0000, 0x010A, 0x0000, -0x0268, 0x0000, 0x0534, 0x0000,
    -0x0B90, 0x0000, 0x2806, 0x4000, 0x2806, 0x0000, -0x0B90, 0x0000,
    0x0534, 0x0000, -0x0268, 0x0000, 0x010A, 0x0000, -0x0067, 0x0000,
    0x0023, 0x0000, -0x000A, 0x0000, 0x0002, 0x0000, -0x0001,
)

_FILTER_POS = (0, 60, 115, 98, 122)
_FILTER_NEG = (0, 0, -52, -55, -60)

ATTACK, DECAY, SUSTAIN, RELEASE, OFF = range(5)


def _clamp16(v: int) -> int:
    return -0x8000 if v < -0x8000 else 0x7FFF if v > 0x7FFF else v


def _s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _direct_volume(reg: int) -> int:
    # bit15=0: direct mode, volume/2 in bits 0-14 (sweep mode is unused here)
    return _s16((reg & 0x7FFF) << 1)


def decode_block(block: bytes, s1: int, s2: int) -> tuple[np.ndarray, int, int]:
    """Decode one 16-byte SPU-ADPCM block. Returns (28 samples, s1, s2)."""
    shift = block[0] & 0x0F
    if shift > 12:
        shift = 9
    filt = min((block[0] >> 4) & 0x07, 4)
    f0, f1 = _FILTER_POS[filt], _FILTER_NEG[filt]
    out = []
    for i in range(28):
        byte = block[2 + (i >> 1)]
        nib = (byte >> ((i & 1) * 4)) & 0x0F
        if nib & 8:
            nib -= 16
        pred = s1 * f0 + s2 * f1 + 32
        pred = -(-pred // 64) if pred < 0 else pred // 64  # C-style truncation
        s = _clamp16(((nib << 12) >> shift) + pred)
        out.append(s)
        s2, s1 = s1, s
    return np.array(out, dtype=np.int32), s1, s2


def envelope_step(level: int, counter: int, *, exp: bool, dec: bool, shift: int,
                  step: int) -> tuple[int, int]:
    """One 44.1kHz tick of the ADSR/sweep envelope generator (psx-spx)."""
    adsr_step = 7 - step
    if dec:
        adsr_step = ~adsr_step
    adsr_step <<= max(0, 11 - shift)
    inc = 0x8000 >> max(0, shift - 11)
    if exp and not dec and level > 0x6000:
        if shift < 10:
            adsr_step >>= 2
        elif shift >= 11:
            inc >>= 2
        else:
            adsr_step >>= 1
            inc >>= 1
    elif exp and dec:
        adsr_step = (adsr_step * level) >> 15

    if (step | (shift << 2)) == 0x7F:
        return level, counter  # all-ones rate: the envelope never steps
    inc = max(inc, 1)

    counter += inc
    if not counter & 0x8000:
        return level, counter
    counter = 0
    level += adsr_step
    if not dec:
        level = _clamp16(level)
    elif level < 0:
        level = 0
    return level, counter


class _Voice:
    __slots__ = ("phase", "level", "env_counter", "counter", "addr", "loop_addr",
                 "s1", "s2", "buf", "flags")

    def __init__(self) -> None:
        self.phase = OFF
        self.level = 0
        self.env_counter = 0
        self.counter = 0
        self.addr = 0
        self.loop_addr = 0
        self.s1 = self.s2 = 0
        self.buf = [0] * 31  # 3 previous samples + current 28-sample block
        self.flags = 0


class SPU:
    def __init__(self) -> None:
        self.ram = bytearray(512 * 1024)
        self.regs = [0] * 0x200
        self.voices = [_Voice() for _ in range(24)]
        self._pending_on = 0
        self._pending_off = 0
        # reverb
        self._rev_addr = 0
        self._rev_in = [np.zeros(38, dtype=np.int64), np.zeros(38, dtype=np.int64)]
        self._rev_out = [np.zeros(38, dtype=np.int64), np.zeros(38, dtype=np.int64)]
        self._rev_phase = 0  # parity of the 44.1kHz sample counter
        self._rev_active = False  # reverb buffer still all zeros until first input
        self._decoded: dict = {}

    # ----------------------------------------------------------- registers

    def write_ram(self, addr: int, data: bytes) -> None:
        self._decoded.clear()
        addr &= 0x7FFFF
        end = addr + len(data)
        if end <= len(self.ram):
            self.ram[addr:end] = data
        else:
            for i, b in enumerate(data):
                self.ram[(addr + i) & 0x7FFFF] = b

    def write_reg(self, off: int, value: int) -> None:
        off &= 0x3FE
        self.regs[off >> 1] = value
        if off < 0x180:
            v, r = off >> 4, off & 0xF
            if r == 0xE:
                self.voices[v].loop_addr = value * 8
            elif r == 0xC:
                self.voices[v].level = _s16(value)
        elif off == 0x188:
            self._pending_on |= value
        elif off == 0x18A:
            self._pending_on |= (value & 0xFF) << 16
        elif off == 0x18C:
            self._pending_off |= value
        elif off == 0x18E:
            self._pending_off |= (value & 0xFF) << 16
        elif off == 0x1A2:
            self._rev_addr = value * 4  # in halfwords

    def _reg(self, off: int) -> int:
        return self.regs[off >> 1]

    # -------------------------------------------------------------- voices

    def _load_block(self, v: _Voice) -> None:
        v.flags = self.ram[v.addr + 1]
        if v.flags & 4:
            v.loop_addr = v.addr
        key = (v.addr, v.s1, v.s2)
        hit = self._decoded.get(key)
        if hit is None:
            samples, s1, s2 = decode_block(bytes(self.ram[v.addr:v.addr + 16]), v.s1, v.s2)
            hit = self._decoded[key] = (samples.tolist(), s1, s2)
        samples, v.s1, v.s2 = hit
        v.buf = v.buf[28:31] + samples

    def _key_on(self, n: int) -> None:
        v = self.voices[n]
        v.phase, v.level, v.env_counter = ATTACK, 0, 0
        v.addr = self._reg(n * 16 + 6) * 8
        v.counter = 0
        v.s1 = v.s2 = 0
        v.buf = [0] * 31
        self._load_block(v)

    def _env_params(self, n: int, phase: int):
        a1, a2 = self._reg(n * 16 + 8), self._reg(n * 16 + 0xA)
        if phase == ATTACK:
            return bool(a1 & 0x8000), False, (a1 >> 10) & 0x1F, (a1 >> 8) & 3
        if phase == DECAY:
            return True, True, (a1 >> 4) & 0xF, 0
        if phase == SUSTAIN:
            return bool(a2 & 0x8000), bool(a2 & 0x4000), (a2 >> 8) & 0x1F, (a2 >> 6) & 3
        return bool(a2 & 0x20), True, a2 & 0x1F, 0

    # -------------------------------------------------------------- render

    def render(self, n: int) -> np.ndarray:
        """Advance n samples at 44.1kHz. Returns int16 array of shape (n, 2)."""
        dry = np.zeros((n, 2), dtype=np.int64)
        wet = np.zeros((n, 2), dtype=np.int64)

        if self._pending_off or self._pending_on:
            for i in range(24):
                if self._pending_off >> i & 1 and self.voices[i].phase != OFF:
                    self.voices[i].phase = RELEASE
                    self.voices[i].env_counter = 0
                if self._pending_on >> i & 1:
                    self._key_on(i)
            self._pending_on = self._pending_off = 0

        cnt = self._reg(0x1AA)
        if cnt & 0x8000:
            eon = self._reg(0x198) | (self._reg(0x19A) & 0xFF) << 16
            for i, v in enumerate(self.voices):
                if v.phase == OFF and v.level == 0:
                    continue
                self._render_voice(i, v, n, dry, wet if eon >> i & 1 else None)

        if not cnt & 0x4000:
            dry[:] = 0
        rev = self._reverb(np.clip(wet, -0x8000, 0x7FFF))
        mix = np.clip(np.clip(dry, -0x8000, 0x7FFF) + rev, -0x8000, 0x7FFF)
        mvol = np.array([_direct_volume(self._reg(0x180)), _direct_volume(self._reg(0x182))])
        out = np.clip((mix * mvol) >> 15, -0x8000, 0x7FFF)
        return out.astype(np.int16)

    def _env_consts(self, n_voice: int, phase: int) -> tuple:
        """Per-phase envelope constants, precomputed as in envelope_step()."""
        exp, dec, shift, st = self._env_params(n_voice, phase)
        frozen = (st | (shift << 2)) == 0x7F
        adsr_step = 7 - st
        if dec:
            adsr_step = ~adsr_step
        adsr_step <<= max(0, 11 - shift)
        inc = 0x8000 >> max(0, shift - 11)
        # exponential increase above 6000h runs slower
        slow_step, slow_inc = adsr_step, inc
        if shift < 10:
            slow_step >>= 2
        elif shift >= 11:
            slow_inc >>= 2
        else:
            slow_step >>= 1
            slow_inc >>= 1
        return (frozen, exp, dec, adsr_step, max(inc, 1), slow_step, max(slow_inc, 1))

    def _render_voice(self, n_voice: int, v: _Voice, n: int, dry, wet) -> None:
        base = n_voice * 16
        vol_l = _direct_volume(self._reg(base))
        vol_r = _direct_volume(self._reg(base + 2))
        step = min(self._reg(base + 4), 0x4000)
        g = GAUSS
        out = [0] * n
        sustain_level = min(((self._reg(base + 8) & 0xF) + 1) * 0x800, 0x7FFF)
        frozen, exp, dec, a_step, a_inc, s_step, s_inc = self._env_consts(n_voice, v.phase)
        phase, level, cnt, c, b = v.phase, v.level, v.env_counter, v.counter, v.buf
        block_end = 28 << 12

        for k in range(n):
            if phase == OFF and level == 0:
                break
            # 4-point gaussian interpolation
            idx = c >> 12
            i = (c >> 4) & 0xFF
            smp = ((g[0xFF - i] * b[idx]) >> 15) + ((g[0x1FF - i] * b[idx + 1]) >> 15) \
                + ((g[0x100 + i] * b[idx + 2]) >> 15) + ((g[i] * b[idx + 3]) >> 15)
            out[k] = (smp * level) >> 15

            # pitch counter / block advance
            c += step
            if c >= block_end:
                c -= block_end
                flags = v.flags
                v.addr = (v.addr + 16) & 0x7FFFF
                if flags & 1:
                    v.addr = v.loop_addr
                    if not flags & 2:
                        phase, level, cnt = RELEASE, 0, 0
                        frozen, exp, dec, a_step, a_inc, s_step, s_inc = \
                            self._env_consts(n_voice, RELEASE)
                self._load_block(v)
                b = v.buf

            # envelope tick (see envelope_step)
            if not frozen:
                if exp and dec:
                    st_, inc_ = (a_step * level) >> 15, a_inc
                elif exp and level > 0x6000:
                    st_, inc_ = s_step, s_inc
                else:
                    st_, inc_ = a_step, a_inc
                cnt += inc_
                if cnt & 0x8000:
                    cnt = 0
                    level += st_
                    if dec:
                        if level < 0:
                            level = 0
                    elif level > 0x7FFF:
                        level = 0x7FFF
                    elif level < -0x8000:
                        level = -0x8000

            if phase == ATTACK:
                if level >= 0x7FFF:
                    phase, cnt = DECAY, 0
                    frozen, exp, dec, a_step, a_inc, s_step, s_inc = self._env_consts(n_voice, DECAY)
            elif phase == DECAY:
                if level <= sustain_level:
                    phase, cnt = SUSTAIN, 0
                    frozen, exp, dec, a_step, a_inc, s_step, s_inc = \
                        self._env_consts(n_voice, SUSTAIN)
            elif phase == RELEASE and level <= 0:
                phase, level = OFF, 0

        v.phase, v.level, v.env_counter, v.counter = phase, level, cnt, c
        samples = np.array(out, dtype=np.int64)
        left = (samples * vol_l) >> 15
        right = (samples * vol_r) >> 15
        dry[:, 0] += left
        dry[:, 1] += right
        if wet is not None:
            wet[:, 0] += left
            wet[:, 1] += right

    # -------------------------------------------------------------- reverb

    def _reverb(self, wet: np.ndarray) -> np.ndarray:
        """Reverb unit: 44.1k -> 22.05k FIR downsample, the psx-spx reverb
        formula at 22.05k, then FIR upsample back to 44.1k."""
        n = len(wet)
        fir = np.array(FIR, dtype=np.int64)
        out = np.zeros((n, 2), dtype=np.int64)
        if n == 0:
            return out
        if not self._rev_active:
            if not wet.any():
                # nothing ever entered the (zero-filled) buffer: output is
                # silence, only the buffer position and the FIR phase move on
                odd = (n + self._rev_phase) // 2  # samples where the reverb would run
                self._rev_phase = (self._rev_phase + n) & 1
                base = (self._reg(0x1A2) * 4) & 0x3FFFF
                size = 0x40000 - base
                cur = max(self._rev_addr, base)
                self._rev_addr = base + (cur - base + odd) % size
                return out
            self._rev_active = True

        # which of these samples are "odd" (reverb runs on every other sample)
        parity = (self._rev_phase + np.arange(n)) & 1
        self._rev_phase = (self._rev_phase + n) & 1

        downs = []
        for ch in range(2):
            hist = np.concatenate([self._rev_in[ch], wet[:, ch]])
            self._rev_in[ch] = hist[-38:]
            filt = np.convolve(hist, fir[::-1], mode="valid") >> 15  # len n
            downs.append(np.clip(filt, -0x8000, 0x7FFF))

        cnt = self._reg(0x1AA)
        write_enable = bool(cnt & 0x80)
        reverb_out = np.zeros((n, 2), dtype=np.int64)
        odd = np.nonzero(parity)[0]
        if len(odd):
            l_in = downs[0][odd].tolist()
            r_in = downs[1][odd].tolist()
            lo, ro = self._reverb_core(l_in, r_in, write_enable)
            reverb_out[odd, 0] = lo
            reverb_out[odd, 1] = ro

        for ch in range(2):
            hist = np.concatenate([self._rev_out[ch], reverb_out[:, ch]])
            self._rev_out[ch] = hist[-38:]
            up = np.convolve(hist, fir[::-1], mode="valid") >> 14
            out[:, ch] = np.clip(up, -0x8000, 0x7FFF)
        return out

    def _reverb_core(self, l_in: list[int], r_in: list[int], write_enable: bool):
        r = self._reg
        base = (r(0x1A2) * 4) & 0x3FFFF  # halfword index of ESA
        size = 0x40000 - base
        mem = memoryview(self.ram).cast("h")

        def a(reg: int) -> int:
            return r(reg) * 4

        d_apf1, d_apf2 = a(0x1C0), a(0x1C2)
        v_iir, v_comb1, v_comb2 = _s16(r(0x1C4)), _s16(r(0x1C6)), _s16(r(0x1C8))
        v_comb3, v_comb4, v_wall = _s16(r(0x1CA)), _s16(r(0x1CC)), _s16(r(0x1CE))
        v_apf1, v_apf2 = _s16(r(0x1D0)), _s16(r(0x1D2))
        m_lsame, m_rsame, m_lcomb1, m_rcomb1 = a(0x1D4), a(0x1D6), a(0x1D8), a(0x1DA)
        m_lcomb2, m_rcomb2, d_lsame, d_rsame = a(0x1DC), a(0x1DE), a(0x1E0), a(0x1E2)
        m_ldiff, m_rdiff, m_lcomb3, m_rcomb3 = a(0x1E4), a(0x1E6), a(0x1E8), a(0x1EA)
        m_lcomb4, m_rcomb4, d_ldiff, d_rdiff = a(0x1EC), a(0x1EE), a(0x1F0), a(0x1F2)
        m_lapf1, m_rapf1, m_lapf2, m_rapf2 = a(0x1F4), a(0x1F6), a(0x1F8), a(0x1FA)
        v_lin, v_rin = _s16(r(0x1FC)), _s16(r(0x1FE))
        evol_l, evol_r = _s16(r(0x184)), _s16(r(0x186))

        cur = self._rev_addr
        if cur < base:
            cur = base
        rel = cur - base
        out_l, out_r = [], []
        clamp = _clamp16

        # every address is relative to the current buffer position and wraps
        # inside ESA..7FFFEh; "[x-2]" in psx-spx is the previous halfword
        o_lsame, o_rsame, o_ldiff, o_rdiff = m_lsame, m_rsame, m_ldiff, m_rdiff
        o_lsame1, o_rsame1, o_ldiff1, o_rdiff1 = m_lsame - 1, m_rsame - 1, m_ldiff - 1, m_rdiff - 1
        combs_l = (m_lcomb1, m_lcomb2, m_lcomb3, m_lcomb4)
        combs_r = (m_rcomb1, m_rcomb2, m_rcomb3, m_rcomb4)
        vc1, vc2, vc3, vc4 = v_comb1, v_comb2, v_comb3, v_comb4
        o_lapf1d, o_rapf1d = m_lapf1 - d_apf1, m_rapf1 - d_apf1
        o_lapf2d, o_rapf2d = m_lapf2 - d_apf2, m_rapf2 - d_apf2

        for lin_raw, rin_raw in zip(l_in, r_in):
            def at(off, _rel=rel):
                return base + (_rel + off) % size

            lin = (v_lin * lin_raw) >> 15
            rin = (v_rin * rin_raw) >> 15

            if write_enable:
                prev = mem[at(o_lsame1)]
                mem[at(o_lsame)] = clamp(clamp((((lin + ((mem[at(d_lsame)] * v_wall) >> 15) - prev)
                                                * v_iir) >> 15) + prev))
                prev = mem[at(o_rsame1)]
                mem[at(o_rsame)] = clamp(clamp((((rin + ((mem[at(d_rsame)] * v_wall) >> 15) - prev)
                                                * v_iir) >> 15) + prev))
                prev = mem[at(o_ldiff1)]
                mem[at(o_ldiff)] = clamp(clamp((((lin + ((mem[at(d_rdiff)] * v_wall) >> 15) - prev)
                                                * v_iir) >> 15) + prev))
                prev = mem[at(o_rdiff1)]
                mem[at(o_rdiff)] = clamp(clamp((((rin + ((mem[at(d_ldiff)] * v_wall) >> 15) - prev)
                                                * v_iir) >> 15) + prev))

            lo = ((vc1 * mem[at(combs_l[0])]) >> 15) + ((vc2 * mem[at(combs_l[1])]) >> 15) \
                + ((vc3 * mem[at(combs_l[2])]) >> 15) + ((vc4 * mem[at(combs_l[3])]) >> 15)
            ro = ((vc1 * mem[at(combs_r[0])]) >> 15) + ((vc2 * mem[at(combs_r[1])]) >> 15) \
                + ((vc3 * mem[at(combs_r[2])]) >> 15) + ((vc4 * mem[at(combs_r[3])]) >> 15)

            t = mem[at(o_lapf1d)]
            lo = clamp(lo - ((v_apf1 * t) >> 15))
            if write_enable:
                mem[at(m_lapf1)] = lo
            lo = clamp(((lo * v_apf1) >> 15) + t)
            t = mem[at(o_rapf1d)]
            ro = clamp(ro - ((v_apf1 * t) >> 15))
            if write_enable:
                mem[at(m_rapf1)] = ro
            ro = clamp(((ro * v_apf1) >> 15) + t)

            t = mem[at(o_lapf2d)]
            lo = clamp(lo - ((v_apf2 * t) >> 15))
            if write_enable:
                mem[at(m_lapf2)] = lo
            lo = clamp(((lo * v_apf2) >> 15) + t)
            t = mem[at(o_rapf2d)]
            ro = clamp(ro - ((v_apf2 * t) >> 15))
            if write_enable:
                mem[at(m_rapf2)] = ro
            ro = clamp(((ro * v_apf2) >> 15) + t)

            out_l.append((lo * evol_l) >> 15)
            out_r.append((ro * evol_r) >> 15)
            rel = (rel + 1) % size

        cur = base + rel
        self._rev_addr = cur
        mem.release()
        return out_l, out_r
