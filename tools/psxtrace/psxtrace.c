/*
 * psxtrace - minimal PS1 machine that boots a BIOS image (no disc) and
 * records everything the BIOS sends to the GPU and the SPU.
 *
 * Only the hardware the boot intro touches is modelled: R3000A CPU,
 * interrupt controller, DMA (GPU/SPU/OTC), root counters, a GPU/SPU that
 * just log what they receive, and a CD-ROM controller with an empty drive.
 *
 * Output trace (little endian), header "PSXTRC01" then records:
 *   'V' u64 cycle                      vblank (end of a displayed field)
 *   'G' u32 word                       GP0 write
 *   'g' u32 word                       GP1 write
 *   'S' u64 cycle u16 reg u16 value    SPU register write (reg = offset from 1F801C00)
 *   'R' u64 cycle u32 addr u32 len ... SPU RAM upload (len bytes)
 *
 * Recording stops when the BIOS shell first talks to the CD-ROM drive: that
 * is the end of the Sony Computer Entertainment screen.
 *
 * usage: psxtrace <bios.bin> <out.trace> [max_fields]
 *
 * Debug environment variables (BIOS analysis):
 *   CDLOG=1             log CD-ROM commands
 *   BRK=<addr>          log registers when the CPU reaches addr (BRKMEM=<addr> also dumps a RAM word)
 *   WATCH=<addr>        log writes to a RAM word
 *   IOLOG=<from>,<to>   log I/O reads in a field range
 *   PROF=<from>,<to>    profile CPU time per 256-byte code page in a field range
 *   DUMPRAM=<file>      dump main RAM at the end
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CPU_HZ 33868800ULL
#define CYCLES_PER_LINE 2153 /* 3413 video clocks * 7/11 */

static uint8_t ram[2 * 1024 * 1024];
static uint8_t bios[512 * 1024];
static uint8_t scratch[1024];
static uint8_t spu_ram[512 * 1024];

static FILE *out;
static uint64_t cycles;
static uint32_t cur_pc_dbg; /* PC of the current instruction, for debug logs */

/* ------------------------------------------------------------------ trace */

static void w8(uint8_t v) { fputc(v, out); }
static void w16(uint16_t v) { fwrite(&v, 2, 1, out); }
static void w32(uint32_t v) { fwrite(&v, 4, 1, out); }
static void w64(uint64_t v) { fwrite(&v, 8, 1, out); }

/* --------------------------------------------------------------- irq ctrl */

static uint32_t i_stat, i_mask;
static void irq(int n) { i_stat |= 1u << n; }

/* -------------------------------------------------------------------- gpu */

static uint32_t gpu_dma_dir, gpu_display_mode, gpu_display_off = 1;
static uint32_t gpu_texpage;
static int gpu_field;
static int cur_line; /* scanline within the current field */
static uint64_t gp0_count;

/* VRAM is tracked only for CPU<->VRAM transfers (A0h/C0h) so that GPUREAD
 * returns what the BIOS uploaded earlier (it reads CLUTs back to fade them). */
static uint16_t vram[512][1024];
static int gp0_words_left, gp0_mode; /* 1 = collecting A0 header, 2 = A0 data, 3 = C0 header */
static uint32_t gp0_hdr[3];
static int gp0_hdr_n;
static uint32_t xfer_x, xfer_y, xfer_w, xfer_h, xfer_i;
static uint32_t read_x, read_y, read_w, read_h, read_i, read_left;

static void xfer_setup(uint32_t xy, uint32_t wh, uint32_t *x, uint32_t *y, uint32_t *w, uint32_t *h) {
    *x = xy & 0x3FF; *y = (xy >> 16) & 0x1FF;
    *w = ((wh & 0xFFFF) - 1) & 0x3FF; *w += 1;
    *h = (((wh >> 16) - 1) & 0x1FF) + 1;
}

static void vram_put(uint16_t px) {
    uint32_t x = (xfer_x + xfer_i % xfer_w) & 0x3FF, y = (xfer_y + xfer_i / xfer_w) & 0x1FF;
    vram[y][x] = px; xfer_i++;
}

static void gp0(uint32_t v) {
    w8('G'); w32(v); gp0_count++;
    if (gp0_mode == 2) {
        vram_put(v & 0xFFFF);
        if (xfer_i < xfer_w * xfer_h) vram_put(v >> 16);
        if (xfer_i >= xfer_w * xfer_h) gp0_mode = 0;
        return;
    }
    if (gp0_mode == 1 || gp0_mode == 3) {
        gp0_hdr[gp0_hdr_n++] = v;
        if (gp0_hdr_n == 3) {
            if (gp0_mode == 1) {
                xfer_setup(gp0_hdr[1], gp0_hdr[2], &xfer_x, &xfer_y, &xfer_w, &xfer_h);
                xfer_i = 0; gp0_mode = 2;
            } else {
                xfer_setup(gp0_hdr[1], gp0_hdr[2], &read_x, &read_y, &read_w, &read_h);
                read_i = 0; read_left = (read_w * read_h + 1) / 2; gp0_mode = 0;
            }
        }
        return;
    }
    if (gp0_words_left > 0) { gp0_words_left--; return; }
    uint32_t op = v >> 24;
    if (op >= 0xA0 && op < 0xC0) { gp0_mode = 1; gp0_hdr[0] = v; gp0_hdr_n = 1; return; }
    if (op >= 0xC0 && op < 0xE0) { gp0_mode = 3; gp0_hdr[0] = v; gp0_hdr_n = 1; return; }
    int n = 0;
    if (op == 0x02) n = 2;
    else if (op >= 0x20 && op < 0x40) {
        int g = op >> 4 & 1, q = op >> 3 & 1, t = op >> 2 & 1, nv = q ? 4 : 3;
        n = nv * (1 + t + g) + (1 - g) - 1;
    } else if (op >= 0x40 && op < 0x60) {
        n = (op & 0x10) ? 3 : 2; /* polylines unsupported (not used by the intro) */
        if (op & 0x08) fprintf(stderr, "polyline unsupported\n");
    } else if (op >= 0x60 && op < 0x80) {
        int sz = op >> 3 & 3, t = op >> 2 & 1; n = 1 + t + (sz == 0);
    } else if (op >= 0x80 && op < 0xA0) n = 3;
    gp0_words_left = n;
}

static uint32_t gpuread(void) {
    if (!read_left) return 0;
    uint32_t r = 0;
    for (int k = 0; k < 2; k++) {
        uint32_t x = (read_x + read_i % read_w) & 0x3FF, y = (read_y + read_i / read_w) & 0x1FF;
        r |= (uint32_t)vram[y][x] << (16 * k); read_i++;
    }
    read_left--;
    return r;
}

static void gp1(uint32_t v) {
    w8('g'); w32(v);
    switch (v >> 24) {
    case 0x00: gpu_dma_dir = 0; gpu_display_off = 1; gpu_display_mode = 0; gp0_mode = 0; gp0_words_left = 0; break;
    case 0x01: gp0_mode = 0; gp0_words_left = 0; break;
    case 0x03: gpu_display_off = v & 1; break;
    case 0x04: gpu_dma_dir = v & 3; break;
    case 0x08: gpu_display_mode = v & 0xFF; break;
    }
}

static int gpu_interlaced(void) { return (gpu_display_mode >> 5) & 1; }

static uint32_t gpustat(void) {
    uint32_t s = 0x1C000000; /* ready for cmd, vram->cpu, dma block */
    s |= gpu_texpage & 0x7FF;
    s |= (gpu_display_mode & 0x3F) << 17;
    s |= ((gpu_display_mode >> 6) & 1) << 16;
    s |= (gpu_display_off & 1) << 23;
    s |= gpu_dma_dir << 29;
    switch (gpu_dma_dir) {
    case 1: s |= 1u << 25; break;
    case 2: s |= ((s >> 28) & 1) << 25; break;
    case 3: s |= ((s >> 27) & 1) << 25; break;
    }
    int in_vblank = cur_line >= 240 || cur_line < 16;
    if (gpu_interlaced()) {
        s |= (uint32_t)gpu_field << 13;
        if (!in_vblank && (gpu_display_mode & 4)) s |= (uint32_t)gpu_field << 31;
    } else {
        s |= 1u << 13;
        if (!in_vblank) s |= (uint32_t)(cur_line & 1) << 31;
    }
    return s;
}

/* -------------------------------------------------------------------- spu */

static uint16_t spu_regs[0x200];
static uint32_t spu_addr; /* current transfer address (bytes) */
static uint64_t spu_writes;

static void spu_ram_write(uint32_t addr, const uint8_t *data, uint32_t len) {
    w8('R'); w64(cycles); w32(addr); w32(len); fwrite(data, 1, len, out);
    for (uint32_t i = 0; i < len; i++) spu_ram[(addr + i) & 0x7FFFF] = data[i];
}

/* Voice timing model: just enough (ADPCM block flags, pitch counter, ADSR)
 * for ENVX/ENDX reads to answer like real hardware. No audio is produced. */
enum { PH_OFF, PH_ATTACK, PH_DECAY, PH_SUSTAIN, PH_RELEASE };
typedef struct {
    int phase;
    int32_t level, counter_env;
    uint32_t pitch_counter, addr, loop_addr;
    int block_started;
} Voice;
static Voice voices[24];
static uint32_t endx;

static uint16_t vreg(int v, int r) { return spu_regs[(v * 0x10 + r) >> 1]; }

static void env_params(Voice *vc, int v, int *mode_exp, int *dec, int *shift, int *stepv, int32_t *target) {
    uint16_t a1 = vreg(v, 8), a2 = vreg(v, 0xA);
    *target = 0;
    switch (vc->phase) {
    case PH_ATTACK: *mode_exp = a1 >> 15; *dec = 0; *shift = (a1 >> 10) & 0x1F; *stepv = (a1 >> 8) & 3; *target = 0x7FFF; break;
    case PH_DECAY: *mode_exp = 1; *dec = 1; *shift = (a1 >> 4) & 0xF; *stepv = 0;
        *target = ((a1 & 0xF) + 1) * 0x800; if (*target > 0x7FFF) *target = 0x7FFF; break;
    case PH_SUSTAIN: *mode_exp = a2 >> 15; *dec = (a2 >> 14) & 1; *shift = (a2 >> 8) & 0x1F; *stepv = (a2 >> 6) & 3; break;
    default: *mode_exp = (a2 >> 5) & 1; *dec = 1; *shift = a2 & 0x1F; *stepv = 0; break;
    }
}

/* one 44.1kHz envelope tick, as described in psx-spx "Envelope Operation" */
static void env_tick(Voice *vc, int v) {
    if (vc->phase == PH_OFF) return;
    int ex, dec, shift, stepv; int32_t target;
    env_params(vc, v, &ex, &dec, &shift, &stepv, &target);
    int32_t step = 7 - stepv;
    if (dec) step = ~step;
    step <<= (11 - shift) > 0 ? (11 - shift) : 0;
    int32_t inc = 0x8000 >> ((shift - 11) > 0 ? (shift - 11) : 0);
    if (ex && !dec && vc->level > 0x6000) {
        if (shift < 10) step >>= 2;
        else if (shift >= 11) inc >>= 2;
        else { step >>= 1; inc >>= 1; }
    } else if (ex && dec) {
        step = (step * vc->level) >> 15;
    }
    int all = dec ? ((stepv | (shift << 2)) == 0x7F && 0) : 0; (void)all;
    if (inc < 1) inc = 1;
    vc->counter_env += inc;
    if (!(vc->counter_env & 0x8000)) goto phase;
    vc->counter_env = 0;
    vc->level += step;
    if (!dec) { if (vc->level > 0x7FFF) vc->level = 0x7FFF; if (vc->level < -0x8000) vc->level = -0x8000; }
    else if (vc->level < 0) vc->level = 0;
phase:
    switch (vc->phase) {
    case PH_ATTACK: if (vc->level >= 0x7FFF) { vc->phase = PH_DECAY; vc->counter_env = 0; } break;
    case PH_DECAY: if (vc->level <= target) { vc->phase = PH_SUSTAIN; vc->counter_env = 0; } break;
    case PH_RELEASE: if (vc->level <= 0) { vc->level = 0; vc->phase = PH_OFF; } break;
    }
}

static void voice_tick(int v) {
    Voice *vc = &voices[v];
    if (vc->phase == PH_OFF && vc->level == 0) { /* still advances, but nothing observable */ }
    if (!vc->block_started) {
        uint8_t flags = spu_ram[(vc->addr + 1) & 0x7FFFF];
        if (flags & 4) vc->loop_addr = vc->addr;
        vc->block_started = 1;
    }
    uint32_t step = vreg(v, 4);
    if (step > 0x4000) step = 0x4000;
    vc->pitch_counter += step;
    while ((vc->pitch_counter >> 12) >= 28) {
        vc->pitch_counter -= 28 << 12;
        uint8_t flags = spu_ram[(vc->addr + 1) & 0x7FFFF];
        vc->addr = (vc->addr + 16) & 0x7FFFF;
        if (flags & 1) {
            endx |= 1u << v;
            vc->addr = vc->loop_addr;
            if (!(flags & 2)) { vc->phase = PH_RELEASE == vc->phase ? PH_OFF : PH_OFF; vc->level = 0; }
        }
        uint8_t nf = spu_ram[(vc->addr + 1) & 0x7FFFF];
        if (nf & 4) vc->loop_addr = vc->addr;
    }
    env_tick(vc, v);
}

static uint64_t spu_next_tick = 768;
static void spu_run(void) {
    while (cycles >= spu_next_tick) {
        spu_next_tick += 768;
        if (!(spu_regs[0x1AA >> 1] & 0x8000)) continue; /* SPU disabled */
        for (int v = 0; v < 24; v++) voice_tick(v);
    }
}

static void key_on(int v) {
    Voice *vc = &voices[v];
    vc->phase = PH_ATTACK; vc->level = 0; vc->counter_env = 0;
    vc->addr = (uint32_t)vreg(v, 6) * 8; vc->pitch_counter = 0; vc->block_started = 0;
    endx &= ~(1u << v);
}

static void spu_write(uint32_t off, uint16_t v) {
    off &= 0x3FE;
    spu_run();
    w8('S'); w64(cycles); w16(off); w16(v);
    spu_writes++;
    spu_regs[off >> 1] = v;
    if (off < 0x180 && (off & 0xF) == 0xC) voices[off >> 4].level = (int16_t)v;
    if (off < 0x180 && (off & 0xF) == 0xE) voices[off >> 4].loop_addr = (uint32_t)v * 8;
    if (off == 0x188 || off == 0x18A) {
        int base = off == 0x188 ? 0 : 16;
        for (int i = 0; i < 16 && base + i < 24; i++) if (v >> i & 1) key_on(base + i);
    }
    if (off == 0x18C || off == 0x18E) {
        int base = off == 0x18C ? 0 : 16;
        for (int i = 0; i < 16 && base + i < 24; i++)
            if (v >> i & 1 && voices[base + i].phase != PH_OFF) { voices[base + i].phase = PH_RELEASE; voices[base + i].counter_env = 0; }
    }
    if (off == 0x1A6) spu_addr = (uint32_t)v * 8;
    if (off == 0x1A8) {
        uint8_t b[2] = {v & 0xFF, v >> 8};
        spu_ram_write(spu_addr, b, 2);
        spu_addr = (spu_addr + 2) & 0x7FFFF;
    }
}

static unsigned spu_rd_count[0x200];
static uint16_t spu_read(uint32_t off) {
    off &= 0x3FE;
    spu_run();
    spu_rd_count[off >> 1]++;
    if (off < 0x180 && (off & 0xF) == 0xC) return (uint16_t)voices[off >> 4].level;
    if (off < 0x180 && (off & 0xF) == 0xE) return voices[off >> 4].loop_addr / 8;
    if (off == 0x19C) return endx & 0xFFFF;
    if (off == 0x19E) return endx >> 16;
    if (off == 0x1AE) return spu_regs[0x1AA >> 1] & 0x3F; /* SPUSTAT mirrors SPUCNT */
    if (off == 0x1A8) return 0;
    return spu_regs[off >> 1];
}

/* ------------------------------------------------------------------ cdrom */

static uint8_t cd_index, cd_ie, cd_if;
static uint8_t cd_resp[16];
static int cd_resp_len, cd_resp_pos;
static uint8_t cd_params[16];
static int cd_nparams;
/* a pending second response (INT2/INT5) */
static uint64_t cd_pending_at;
static uint8_t cd_pending_int, cd_pending[8];
static int cd_pending_len;
static uint64_t cd_first_at;
static uint8_t cd_first_int, cd_first[8];
static int cd_first_len;
static const uint8_t CD_STAT = 0x10; /* shell open: no disc */

static void cd_queue_first(uint8_t intn, const uint8_t *r, int n) {
    cd_first_at = cycles + 25000; cd_first_int = intn;
    memcpy(cd_first, r, n); cd_first_len = n;
}
static void cd_queue_second(uint8_t intn, const uint8_t *r, int n) {
    cd_pending_at = cycles + 25000 + 50000; cd_pending_int = intn;
    memcpy(cd_pending, r, n); cd_pending_len = n;
}

static int cd_used; /* the shell talks to the drive only after the Sony screen */
static void cd_command(uint8_t c) {
    cd_used = 1;
    if (getenv("CDLOG")) fprintf(stderr, "[cd cmd %02x np=%d p0=%02x frame~%llu]\n", c, cd_nparams, cd_params[0], (unsigned long long)(cycles / 565000));
    uint8_t st[1] = {CD_STAT};
    switch (c) {
    case 0x19: { /* Test */
        if (cd_nparams && cd_params[0] == 0x20) {
            uint8_t r[4] = {0x94, 0x09, 0x19, 0xC0};
            cd_queue_first(3, r, 4);
        } else cd_queue_first(3, st, 1);
        break;
    }
    case 0x1A: { /* GetID: no disc */
        cd_queue_first(3, st, 1);
        uint8_t r[8] = {0x08, 0x40, 0, 0, 0, 0, 0, 0};
        cd_queue_second(5, r, 8);
        break;
    }
    case 0x0A: /* Init */
    case 0x09: /* Pause */
    case 0x08: /* Stop */
    case 0x07: /* MotorOn */
        cd_queue_first(3, st, 1);
        cd_queue_second(2, st, 1);
        break;
    default:
        cd_queue_first(3, st, 1);
        break;
    }
    cd_nparams = 0;
}

static void cd_deliver(uint8_t intn, const uint8_t *r, int n) {
    memcpy(cd_resp, r, n); cd_resp_len = n; cd_resp_pos = 0;
    cd_if = (cd_if & ~7) | intn;
    if (cd_ie & cd_if & 0x1F) irq(2);
}

static void cd_tick(void) {
    if (cd_first_at && cycles >= cd_first_at && (cd_if & 7) == 0) {
        cd_first_at = 0;
        cd_deliver(cd_first_int, cd_first, cd_first_len);
    } else if (!cd_first_at && cd_pending_at && cycles >= cd_pending_at && (cd_if & 7) == 0) {
        cd_pending_at = 0;
        cd_deliver(cd_pending_int, cd_pending, cd_pending_len);
    }
}

static uint8_t cd_read(uint32_t a) {
    switch (a & 3) {
    case 0: {
        uint8_t s = cd_index;
        s |= (cd_nparams == 0) << 3;     /* param fifo empty */
        s |= (cd_nparams < 16) << 4;     /* param fifo not full */
        s |= (cd_resp_pos < cd_resp_len) << 5;
        s |= (cd_first_at != 0) << 7;    /* busy */
        return s;
    }
    case 1: return cd_resp_pos < cd_resp_len ? cd_resp[cd_resp_pos++] : 0;
    case 2: return 0;
    default: return (cd_index & 1) ? (cd_if | 0xE0) : (cd_ie | 0xE0);
    }
}

static void cd_write(uint32_t a, uint8_t v) {
    switch ((a & 3) | (cd_index << 2)) {
    case 0: case 4: case 8: case 12: cd_index = v & 3; break;
    case 1: cd_command(v); break;
    case 2: if (cd_nparams < 16) cd_params[cd_nparams++] = v; break;
    case 6: cd_ie = v & 0x1F; break;
    case 7: cd_if &= ~(v & 0x1F); if (v & 0x40) cd_nparams = 0; break;
    default: break;
    }
}

/* ----------------------------------------------------------------- timers */

typedef struct { uint32_t count, mode, target; int irq_done; } Timer;
static Timer timers[3];
static uint32_t t2_div;

static void timer_step(int n, uint32_t ticks) {
    Timer *t = &timers[n];
    while (ticks--) {
        t->count = (t->count + 1) & 0xFFFF;
        int hit = 0;
        if (t->count == t->target) {
            t->mode |= 1u << 11;
            if (t->mode & (1u << 4)) hit = 1;
            if (t->mode & (1u << 3)) t->count = 0;
        }
        if (t->count == 0 && !(t->mode & (1u << 3) && t->target == 0)) {
            if (t->count == 0) { t->mode |= 1u << 12; if (t->mode & (1u << 5)) hit = 1; }
        }
        if (hit && (!t->irq_done || (t->mode & (1u << 6)))) {
            t->irq_done = 1;
            if (t->mode & (1u << 7)) t->mode ^= 1u << 10; else t->mode &= ~(1u << 10);
            if (!(t->mode & (1u << 10))) irq(4 + n);
            if (!(t->mode & (1u << 7))) t->mode |= 1u << 10;
        }
    }
}

/* -------------------------------------------------------------------- dma */

static uint32_t dma_madr[7], dma_bcr[7], dma_chcr[7], dpcr = 0x07654321, dicr;

static void dicr_update(void) {
    int force = (dicr >> 15) & 1;
    int en = (dicr >> 23) & 1;
    uint32_t flags = (dicr >> 24) & 0x7F, mask = (dicr >> 16) & 0x7F;
    uint32_t old = dicr >> 31;
    uint32_t m = force || (en && (flags & mask));
    dicr = (dicr & 0x7FFFFFFF) | (m << 31);
    if (m && !old) irq(3);
}

static uint32_t ram32(uint32_t a) { uint32_t v; memcpy(&v, &ram[a & 0x1FFFFC], 4); return v; }
static void ram32w(uint32_t a, uint32_t v) { memcpy(&ram[a & 0x1FFFFC], &v, 4); }

static void dma_run(int ch) {
    uint32_t chcr = dma_chcr[ch];
    uint32_t sync = (chcr >> 9) & 3;
    int to_dev = chcr & 1;
    int step = (chcr & 2) ? -4 : 4;
    uint32_t addr = dma_madr[ch] & 0x1FFFFC;

    if (ch == 2 && sync == 2) { /* GPU linked list */
        for (int guard = 0; guard < 1 << 20; guard++) {
            uint32_t hdr = ram32(addr);
            uint32_t n = hdr >> 24;
            if (n && getenv("DBG2C") && (ram32(addr + 4) >> 24) == 0x2c) fprintf(stderr, "[2c packet at %08x clut word %08x]\n", addr, ram32(addr + 12));
            for (uint32_t i = 1; i <= n; i++) gp0(ram32(addr + i * 4));
            if (hdr & 0x800000) break;
            addr = hdr & 0x1FFFFC;
        }
        dma_madr[ch] = 0x00FFFFFF;
    } else {
        uint32_t words = sync == 0 ? ((dma_bcr[ch] & 0xFFFF) ? (dma_bcr[ch] & 0xFFFF) : 0x10000)
                                   : (dma_bcr[ch] & 0xFFFF) * (dma_bcr[ch] >> 16);
        if (ch == 6) {
            for (uint32_t i = 0; i < words; i++) {
                ram32w(addr, i == words - 1 ? 0x00FFFFFF : ((addr - 4) & 0x1FFFFF));
                addr -= 4;
            }
        } else if (ch == 2) {
            for (uint32_t i = 0; i < words; i++) {
                if (to_dev) gp0(ram32(addr)); else ram32w(addr, 0);
                addr += step;
            }
            if (sync == 1) dma_madr[ch] = addr;
        } else if (ch == 4) {
            if (to_dev) {
                uint8_t *buf = malloc(words * 4);
                for (uint32_t i = 0; i < words; i++) { uint32_t w = ram32(addr); memcpy(buf + i * 4, &w, 4); addr += step; }
                spu_ram_write(spu_addr, buf, words * 4);
                spu_addr = (spu_addr + words * 4) & 0x7FFFF;
                free(buf);
            } else {
                for (uint32_t i = 0; i < words; i++) {
                    uint32_t w; memcpy(&w, &spu_ram[spu_addr], 4);
                    ram32w(addr, w); addr += step; spu_addr = (spu_addr + 4) & 0x7FFFF;
                }
            }
            if (sync == 1) dma_madr[ch] = addr;
        } else {
            fprintf(stderr, "dma: unhandled channel %d chcr=%08x\n", ch, chcr);
        }
    }
    dma_chcr[ch] &= ~((1u << 24) | (1u << 28));
    if ((dicr >> (16 + ch)) & 1 && (dicr >> 23) & 1) dicr |= 1u << (24 + ch);
    dicr_update();
}

static void dma_maybe_start(int ch) {
    uint32_t c = dma_chcr[ch];
    uint32_t sync = (c >> 9) & 3;
    int enabled = (dpcr >> (ch * 4 + 3)) & 1;
    int active = (c >> 24) & 1;
    if (sync == 0) active = active && ((c >> 28) & 1);
    if (enabled && active) dma_run(ch);
}

/* -------------------------------------------------------------------- bus */

static long iolog_from = -1, iolog_to = -1;
static uint32_t io_read_inner(uint32_t a, int size);
static uint32_t io_read(uint32_t a, int size) {
    uint32_t v = io_read_inner(a, size);
    long fr = (long)(cycles / 565000);
    if (fr >= iolog_from && fr <= iolog_to) fprintf(stderr, "[io rd %08x = %08x pc %08x f%ld]\n", a, v, cur_pc_dbg, fr);
    return v;
}
static uint32_t io_read_inner(uint32_t a, int size) {
    if (a >= 0x1F801C00 && a < 0x1F802000) {
        if (size == 4) return spu_read(a - 0x1F801C00) | (uint32_t)spu_read(a - 0x1F801C00 + 2) << 16;
        return spu_read(a - 0x1F801C00);
    }
    if (a >= 0x1F801800 && a < 0x1F801804) return cd_read(a);
    if (a >= 0x1F801080 && a < 0x1F801100) {
        int ch = (a >> 4) & 7;
        if (ch == 7) return (a & 0xF) == 0 ? dpcr : (a & 0xF) == 4 ? dicr : 0;
        switch (a & 0xF) { case 0: return dma_madr[ch]; case 4: return dma_bcr[ch]; case 8: return dma_chcr[ch]; }
        return 0;
    }
    if (a >= 0x1F801100 && a < 0x1F801130) {
        Timer *t = &timers[(a >> 4) & 3];
        switch (a & 0xF) {
        case 0: return t->count;
        case 4: { uint32_t m = t->mode; t->mode &= ~(3u << 11); return m; }
        case 8: return t->target;
        }
        return 0;
    }
    switch (a) {
    case 0x1F801070: return i_stat;
    case 0x1F801074: return i_mask;
    case 0x1F801810: return gpuread();
    case 0x1F801814: return gpustat();
    case 0x1F801044: return 0x7; /* JOY_STAT: TX ready, RX fifo "not empty" */
    case 0x1F801040: return 0xFF;
    }
    return 0;
}

static void io_write(uint32_t a, uint32_t v, int size) {
    if (a >= 0x1F801C00 && a < 0x1F802000) {
        spu_write(a - 0x1F801C00, v & 0xFFFF);
        if (size == 4) spu_write(a - 0x1F801C00 + 2, v >> 16);
        return;
    }
    if (a >= 0x1F801800 && a < 0x1F801804) { cd_write(a, v & 0xFF); return; }
    if (a >= 0x1F801080 && a < 0x1F801100) {
        int ch = (a >> 4) & 7;
        if (ch == 7) {
            if ((a & 0xF) == 0) dpcr = v;
            else if ((a & 0xF) == 4) {
                dicr = (dicr & 0xFF000000 & ~(v & 0x7F000000)) | (v & 0x00FF803F);
                dicr_update();
            }
            return;
        }
        switch (a & 0xF) {
        case 0: dma_madr[ch] = v & 0xFFFFFF; break;
        case 4: dma_bcr[ch] = v; break;
        case 8: dma_chcr[ch] = v; dma_maybe_start(ch); break;
        }
        return;
    }
    if (a >= 0x1F801100 && a < 0x1F801130) {
        Timer *t = &timers[(a >> 4) & 3];
        switch (a & 0xF) {
        case 0: t->count = v & 0xFFFF; break;
        case 4: t->mode = (v & 0x3FF) | (1u << 10); t->count = 0; t->irq_done = 0; break;
        case 8: t->target = v & 0xFFFF; break;
        }
        return;
    }
    switch (a) {
    case 0x1F801070: i_stat &= v; return;
    case 0x1F801074: i_mask = v & 0x7FF; return;
    case 0x1F801810: gp0(v); if ((v >> 24) == 0xE1) gpu_texpage = v & 0x7FF; return;
    case 0x1F801814: gp1(v); return;
    case 0x1F802041: fprintf(stderr, "[post %02x]\n", v & 0xFF); return;
    }
}

static int cache_isolated;

/* Timing model: base 1 cycle/instruction plus memory wait states, an
 * instruction cache for cached RAM code, and mult/div latency. */
static uint32_t stall;            /* extra cycles accrued by the current instruction */
static int32_t muldiv_left;       /* cycles until HI/LO are ready */
static uint32_t icache_tag[256];  /* 4 KB direct mapped, 16-byte lines */
static uint8_t icache_valid[256];

static const uint32_t ram_cost = 4;                  /* stores go through the write buffer: free */
static const uint32_t rom_cost[3] = {7, 12, 22};     /* 8/16/32-bit reads on the 8-bit ROM bus */
static uint32_t access_cost(uint32_t a, int size) {
    if (a < 0x00800000) return ram_cost;                  /* main RAM */
    if (a >= 0x1FC00000 && a < 0x1FC80000) return size == 4 ? rom_cost[2] : size == 2 ? rom_cost[1] : rom_cost[0]; /* 8-bit BIOS ROM */
    if (a >= 0x1F800000 && a < 0x1F800400) return 0;      /* scratchpad */
    return 3;                                             /* I/O */
}

static void fetch_cost(uint32_t va) {
    uint32_t a = va & 0x1FFFFFFF;
    int cached = va < 0xA0000000 && a < 0x00800000;
    if (!cached) { stall += access_cost(a, 4); return; }
    uint32_t line = (va >> 4) & 0xFF, tag = va >> 12;
    if (icache_valid[line] && icache_tag[line] == tag) return;
    icache_valid[line] = 1; icache_tag[line] = tag;
    stall += 4 + (4 - ((va >> 2) & 3));
}

static uint32_t phys(uint32_t a) {
    if (a >= 0xC0000000) return a; /* kseg2 */
    return a & 0x1FFFFFFF;
}

static uint32_t mem_read(uint32_t va, int size) {
    uint32_t a = phys(va), v = 0;
    stall += access_cost(a, size);
    const uint8_t *p = NULL;
    if (a < 0x00800000) p = &ram[a & 0x1FFFFF];
    else if (a >= 0x1FC00000 && a < 0x1FC80000) p = &bios[a & 0x7FFFF];
    else if (a >= 0x1F800000 && a < 0x1F800400) p = &scratch[a & 0x3FF];
    else if (a >= 0x1F000000 && a < 0x1F800000) return size == 4 ? 0xFFFFFFFF : size == 2 ? 0xFFFF : 0xFF;
    else if (a >= 0x1F801000 && a < 0x1F803000) {
        if (size == 1 && !(a >= 0x1F801800 && a < 0x1F801804)) return (io_read(a & ~3, 4) >> ((a & 3) * 8)) & 0xFF;
        if (size == 2 && !(a >= 0x1F801C00 && a < 0x1F802000)) return (io_read(a & ~3, 4) >> ((a & 2) * 8)) & 0xFFFF;
        return io_read(a, size);
    } else return 0;
    memcpy(&v, p, size);
    return v;
}

static uint32_t watch_addr = 0xFFFFFFFF;
static void mem_write(uint32_t va, uint32_t v, int size) {
    if (cache_isolated) return;
    uint32_t a = phys(va);
    if ((a & ~3u) == watch_addr) fprintf(stderr, "[watch %08x <- %08x size %d pc %08x frame~%llu]\n", a, v, size, cur_pc_dbg, (unsigned long long)(cycles / 565000));
    uint8_t *p = NULL;
    if (a < 0x00800000) p = &ram[a & 0x1FFFFF];
    else if (a >= 0x1F800000 && a < 0x1F800400) p = &scratch[a & 0x3FF];
    else if (a >= 0x1F801000 && a < 0x1F803000) { io_write(a, v, size); return; }
    else return;
    memcpy(p, &v, size);
}


/* -------------------------------------------------------------------- cpu */

static uint32_t R[32], hi, lo, pc, next_pc;
static uint32_t cop0[32];
static uint32_t pend_reg, pend_val; /* load issued by the previous instruction */
static uint32_t new_reg, new_val;   /* load issued by the current instruction */
static int in_delay, branch_taken;
static uint32_t cur_pc;
static uint64_t cop2_ops;

#define SR cop0[12]
#define CAUSE cop0[13]
#define EPC cop0[14]

static void set_reg(uint32_t r, uint32_t v) {
    if (r == pend_reg) pend_reg = 0; /* a direct write beats the delayed load */
    if (r) R[r] = v;
}

static void load_reg(uint32_t r, uint32_t v) { new_reg = r; new_val = v; }

static void exception(int code) {
    uint32_t vec = (SR & (1u << 22)) ? 0xBFC00180 : 0x80000080;
    SR = (SR & ~0x3Fu) | ((SR << 2) & 0x3F);
    CAUSE = (CAUSE & ~0x7Cu) | ((uint32_t)code << 2);
    if (in_delay) { EPC = cur_pc - 4; CAUSE |= 1u << 31; }
    else { EPC = cur_pc; CAUSE &= ~(1u << 31); }
    pc = vec; next_pc = vec + 4;
    branch_taken = 0;
}

static void branch(uint32_t target) { next_pc = target; branch_taken = 1; }

static void commit_loads(void) {
    if (pend_reg) R[pend_reg] = pend_val;
    pend_reg = new_reg; pend_val = new_val;
    new_reg = 0;
    R[0] = 0;
}

static uint32_t brk_addr = 1;
static void step(void) {
    cur_pc = pc; cur_pc_dbg = pc;

    CAUSE = (CAUSE & ~(1u << 10)) | ((uint32_t)((i_stat & i_mask) != 0) << 10);
    if ((SR & 1) && (SR & CAUSE & 0xFF00)) {
        exception(0);
        commit_loads();
        in_delay = 0;
        return;
    }

    /* BIOS tty (A0:3Ch / B0:3Dh putchar) */
    if ((pc == 0xA0 && R[9] == 0x3C) || (pc == 0xB0 && R[9] == 0x3D)) fputc(R[4] & 0xFF, stderr);

    if (pc == brk_addr) fprintf(stderr, "[brk %08x ra=%08x a0=%08x a1=%08x a2=%08x a3=%08x frame~%llu mem=%08x]\n", pc, R[31], R[4], R[5], R[6], R[7], (unsigned long long)(cycles / 565000), getenv("BRKMEM") ? ram32(strtoul(getenv("BRKMEM"), 0, 16)) : 0);
    uint32_t saved_stall = stall;
    uint32_t op = mem_read(pc, 4);
    stall = saved_stall;
    fetch_cost(pc);
    pc = next_pc;
    next_pc = pc + 4;
    branch_taken = 0;

    uint32_t rs = (op >> 21) & 31, rt = (op >> 16) & 31, rd = (op >> 11) & 31, sh = (op >> 6) & 31;
    uint32_t imm = op & 0xFFFF, simm = (uint32_t)(int32_t)(int16_t)imm;
    uint32_t S = R[rs], T = R[rt];

    switch (op >> 26) {
    case 0x00:
        switch (op & 0x3F) {
        case 0x00: set_reg(rd, T << sh); break;
        case 0x02: set_reg(rd, T >> sh); break;
        case 0x03: set_reg(rd, (uint32_t)((int32_t)T >> sh)); break;
        case 0x04: set_reg(rd, T << (S & 31)); break;
        case 0x06: set_reg(rd, T >> (S & 31)); break;
        case 0x07: set_reg(rd, (uint32_t)((int32_t)T >> (S & 31))); break;
        case 0x08: branch(S); break;
        case 0x09: set_reg(rd, next_pc); branch(S); break;
        case 0x0C: exception(8); break;
        case 0x0D: exception(9); break;
        case 0x10: if (muldiv_left > 0) { stall += muldiv_left; muldiv_left = 0; } set_reg(rd, hi); break;
        case 0x11: hi = S; break;
        case 0x12: if (muldiv_left > 0) { stall += muldiv_left; muldiv_left = 0; } set_reg(rd, lo); break;
        case 0x13: lo = S; break;
        case 0x18: { uint32_t m = (int32_t)T < 0 ? ~T : T; muldiv_left = m < 0x800 ? 6 : m < 0x100000 ? 9 : 13; }
        { int64_t r = (int64_t)(int32_t)S * (int32_t)T; lo = (uint32_t)r; hi = (uint32_t)((uint64_t)r >> 32); } break;
        case 0x19: muldiv_left = T < 0x800 ? 6 : T < 0x100000 ? 9 : 13;
        { uint64_t r = (uint64_t)S * T; lo = (uint32_t)r; hi = (uint32_t)(r >> 32); } break;
        case 0x1A: muldiv_left = 36;
        {
            int32_t n = (int32_t)S, d = (int32_t)T;
            if (d == 0) { hi = (uint32_t)n; lo = n >= 0 ? 0xFFFFFFFF : 1; }
            else if ((uint32_t)n == 0x80000000 && d == -1) { hi = 0; lo = 0x80000000; }
            else { lo = (uint32_t)(n / d); hi = (uint32_t)(n % d); }
        } break;
        case 0x1B: muldiv_left = 36; if (T == 0) { hi = S; lo = 0xFFFFFFFF; } else { lo = S / T; hi = S % T; } break;
        case 0x20: { uint32_t r = S + T; if (!((S ^ T) & 0x80000000) && ((r ^ S) & 0x80000000)) exception(12); else set_reg(rd, r); } break;
        case 0x21: set_reg(rd, S + T); break;
        case 0x22: { uint32_t r = S - T; if (((S ^ T) & 0x80000000) && ((r ^ S) & 0x80000000)) exception(12); else set_reg(rd, r); } break;
        case 0x23: set_reg(rd, S - T); break;
        case 0x24: set_reg(rd, S & T); break;
        case 0x25: set_reg(rd, S | T); break;
        case 0x26: set_reg(rd, S ^ T); break;
        case 0x27: set_reg(rd, ~(S | T)); break;
        case 0x2A: set_reg(rd, (int32_t)S < (int32_t)T); break;
        case 0x2B: set_reg(rd, S < T); break;
        default: fprintf(stderr, "illegal special %08x at %08x\n", op, cur_pc); exception(10); break;
        }
        break;
    case 0x01: {
        int cond = ((int32_t)S < 0) ^ (int)(rt & 1);
        if ((rt & 0x1E) == 0x10) set_reg(31, next_pc);
        if (cond) branch(pc + (simm << 2));
    } break;
    case 0x02: branch((pc & 0xF0000000) | ((op & 0x3FFFFFF) << 2)); break;
    case 0x03: set_reg(31, next_pc); branch((pc & 0xF0000000) | ((op & 0x3FFFFFF) << 2)); break;
    case 0x04: if (S == T) branch(pc + (simm << 2)); break;
    case 0x05: if (S != T) branch(pc + (simm << 2)); break;
    case 0x06: if ((int32_t)S <= 0) branch(pc + (simm << 2)); break;
    case 0x07: if ((int32_t)S > 0) branch(pc + (simm << 2)); break;
    case 0x08: { uint32_t r = S + simm; if (!((S ^ simm) & 0x80000000) && ((r ^ S) & 0x80000000)) exception(12); else set_reg(rt, r); } break;
    case 0x09: set_reg(rt, S + simm); break;
    case 0x0A: set_reg(rt, (int32_t)S < (int32_t)simm); break;
    case 0x0B: set_reg(rt, S < simm); break;
    case 0x0C: set_reg(rt, S & imm); break;
    case 0x0D: set_reg(rt, S | imm); break;
    case 0x0E: set_reg(rt, S ^ imm); break;
    case 0x0F: set_reg(rt, imm << 16); break;
    case 0x10:
        if (rs == 0x00) load_reg(rt, cop0[rd]);
        else if (rs == 0x04) {
            if (rd == 13) CAUSE = (CAUSE & ~0x300u) | (T & 0x300);
            else cop0[rd] = T;
            cache_isolated = (SR >> 16) & 1;
        } else if (rs == 0x10 && (op & 0x3F) == 0x10) {
            SR = (SR & ~0xFu) | ((SR >> 2) & 0xF);
            cache_isolated = (SR >> 16) & 1;
        }
        break;
    case 0x12: case 0x32: case 0x3A:
        if (cop2_ops++ < 5) fprintf(stderr, "GTE op %08x at %08x\n", op, cur_pc);
        break;
    case 0x20: load_reg(rt, (uint32_t)(int32_t)(int8_t)mem_read(S + simm, 1)); break;
    case 0x21: { uint32_t a = S + simm; if (a & 1) exception(4); else load_reg(rt, (uint32_t)(int32_t)(int16_t)mem_read(a, 2)); } break;
    case 0x23: { uint32_t a = S + simm; if (a & 3) exception(4); else load_reg(rt, mem_read(a, 4)); } break;
    case 0x24: load_reg(rt, mem_read(S + simm, 1) & 0xFF); break;
    case 0x25: { uint32_t a = S + simm; if (a & 1) exception(4); else load_reg(rt, mem_read(a, 2) & 0xFFFF); } break;
    case 0x22: case 0x26: {
        uint32_t a = S + simm;
        uint32_t cur = (pend_reg == rt) ? pend_val : R[rt];
        uint32_t w = mem_read(a & ~3u, 4), r;
        if ((op >> 26) == 0x22) {
            switch (a & 3) {
            case 0: r = (cur & 0x00FFFFFF) | (w << 24); break;
            case 1: r = (cur & 0x0000FFFF) | (w << 16); break;
            case 2: r = (cur & 0x000000FF) | (w << 8); break;
            default: r = w; break;
            }
        } else {
            switch (a & 3) {
            case 0: r = w; break;
            case 1: r = (cur & 0xFF000000) | (w >> 8); break;
            case 2: r = (cur & 0xFFFF0000) | (w >> 16); break;
            default: r = (cur & 0xFFFFFF00) | (w >> 24); break;
            }
        }
        load_reg(rt, r);
    } break;
    case 0x28: mem_write(S + simm, T & 0xFF, 1); break;
    case 0x29: { uint32_t a = S + simm; if (a & 1) exception(5); else mem_write(a, T & 0xFFFF, 2); } break;
    case 0x2B: { uint32_t a = S + simm; if (a & 3) exception(5); else mem_write(a, T, 4); } break;
    case 0x2A: case 0x2E: {
        uint32_t a = S + simm, al = a & ~3u;
        uint32_t m = mem_read(al, 4), r;
        if ((op >> 26) == 0x2A) {
            switch (a & 3) {
            case 0: r = (m & 0xFFFFFF00) | (T >> 24); break;
            case 1: r = (m & 0xFFFF0000) | (T >> 16); break;
            case 2: r = (m & 0xFF000000) | (T >> 8); break;
            default: r = T; break;
            }
        } else {
            switch (a & 3) {
            case 0: r = T; break;
            case 1: r = (m & 0x000000FF) | (T << 8); break;
            case 2: r = (m & 0x0000FFFF) | (T << 16); break;
            default: r = (m & 0x00FFFFFF) | (T << 24); break;
            }
        }
        mem_write(al, r, 4);
    } break;
    default:
        fprintf(stderr, "illegal op %08x at %08x\n", op, cur_pc);
        exception(10);
        break;
    }

    commit_loads();
    in_delay = branch_taken;
}

/* ------------------------------------------------------------------- main */

static int prof_on; static long prof_from, prof_to; static uint64_t prof_cyc[0x10000], prof_tot;
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <bios.bin> <out.trace> [frames]\n", argv[0]); return 1; }
    FILE *f = fopen(argv[1], "rb");
    if (!f || fread(bios, 1, sizeof bios, f) != sizeof bios) { fprintf(stderr, "bad bios\n"); return 1; }
    fclose(f);
    out = fopen(argv[2], "wb");
    if (!out) { perror(argv[2]); return 1; }
    long frames = argc > 3 ? atol(argv[3]) : 3000;
    fwrite("PSXTRC01", 1, 8, out);

    if (getenv("IOLOG")) sscanf(getenv("IOLOG"), "%ld,%ld", &iolog_from, &iolog_to);
    if (getenv("PROF")) { prof_on = 1; sscanf(getenv("PROF"), "%ld,%ld", &prof_from, &prof_to); }
    if (getenv("BRK")) brk_addr = strtoul(getenv("BRK"), 0, 16);
    if (getenv("WATCH")) watch_addr = strtoul(getenv("WATCH"), 0, 16);
    pc = 0xBFC00000; next_pc = pc + 4;
    SR = 1u << 22;

    uint64_t next_line = CYCLES_PER_LINE;
    int line = 0;
    long frame = 0;
    while (frame < frames && !cd_used) {
        stall = 0;
        uint32_t prof_pc = pc;
        step();
        uint32_t cost = 1 + stall;
        if (prof_on) { long fr = (long)(cycles / 565000); if (fr >= prof_from && fr <= prof_to) { prof_cyc[(prof_pc >> 8) & 0xFFFF] += cost; prof_tot += cost; } }
        muldiv_left -= cost;
        cycles += cost;
        timer_step(0, cost);
        uint32_t src2 = (timers[2].mode >> 8) & 3;
        if (src2 >= 2) { t2_div += cost; while (t2_div >= 8) { t2_div -= 8; timer_step(2, 1); } }
        else timer_step(2, cost);
        if (!(timers[1].mode & 0x100)) timer_step(1, cost);
        cd_tick();
        if (cycles >= spu_next_tick) spu_run();

        if (cycles >= next_line) {
            next_line += CYCLES_PER_LINE;
            if (timers[1].mode & 0x100) timer_step(1, 1);
            line++;
            cur_line = line;
            int lines = gpu_interlaced() ? (gpu_field ? 262 : 263) : 263;
            if (line == 240) {
                irq(0);
                w8('V'); w64(cycles);
                frame++;
            }
            if (line >= lines) { line = 0; cur_line = 0; gpu_field ^= 1; }
        }
    }
    fclose(out);
    if (prof_on) { for (int i = 0; i < 0x10000; i++) if (prof_cyc[i] * 100 > prof_tot) fprintf(stderr, "prof %04x00: %llu (%.1f%%)\n", i, (unsigned long long)prof_cyc[i], prof_cyc[i] * 100.0 / prof_tot); }
    if (getenv("DUMPRAM")) { FILE *r = fopen(getenv("DUMPRAM"), "wb"); fwrite(ram, 1, sizeof ram, r); fclose(r); }
    if (getenv("SPULOG")) for (int i = 0; i < 0x200; i++) if (spu_rd_count[i]) fprintf(stderr, "spu read %03x x%u\n", i * 2, spu_rd_count[i]);
    fprintf(stderr, "\nframes=%ld cycles=%llu gp0=%llu spu_writes=%llu gte=%llu pc=%08x\n",
            frame, (unsigned long long)cycles, (unsigned long long)gp0_count,
            (unsigned long long)spu_writes, (unsigned long long)cop2_ops, pc);
    return 0;
}
