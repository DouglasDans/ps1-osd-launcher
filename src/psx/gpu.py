"""Software model of the PS1 GPU, limited to what the BIOS boot intro uses.

The GPU draws into a 1024x512 VRAM of 15-bit pixels (bit 15 = mask bit).
Rasterization follows psx-spx: polygons are split into triangles, pixels are
sampled at integer coordinates with a top-left fill rule (so right/bottom
edges are excluded), gouraud colors and texture coordinates are interpolated
linearly, and 24-bit colors are reduced to 15-bit with the 4x4 dither matrix.
"""

import logging

import numpy as np

log = logging.getLogger("ps1.psx.gpu")

VRAM_W, VRAM_H = 1024, 512

DITHER = np.array(
    [
        [-4, 0, -3, 1],
        [2, -2, 3, -1],
        [-3, 1, -4, 0],
        [3, -1, 2, -2],
    ],
    dtype=np.int32,
)

# horizontal resolution -> video clocks per pixel (GP1(08h) bits 0-1, bit 6)
_DOTCLOCK_DIV = {0: 10, 1: 8, 2: 5, 3: 4}


def rgb15(r: int, g: int, b: int) -> int:
    return (r >> 3) | (g >> 3) << 5 | (b >> 3) << 10


def rgb8(px: np.ndarray) -> np.ndarray:
    """15-bit pixels -> (..., 3) 8-bit channels, expanded like the video DAC."""
    c = np.stack([px & 0x1F, px >> 5 & 0x1F, px >> 10 & 0x1F], axis=-1).astype(np.uint8)
    return (c << 3) | (c >> 2)


def _sign11(v: int) -> int:
    v &= 0x7FF
    return v - 0x800 if v & 0x400 else v


def _packet_length(op: int) -> int:
    if op == 0x02:
        return 3
    if 0x20 <= op < 0x40:
        gouraud, quad, textured = op >> 4 & 1, op >> 3 & 1, op >> 2 & 1
        verts = 4 if quad else 3
        return verts * (1 + textured + gouraud) + (1 - gouraud)
    if 0x40 <= op < 0x60:
        return 4 if op & 0x10 else 3
    if 0x60 <= op < 0x80:
        size, textured = op >> 3 & 3, op >> 2 & 1
        return 2 + textured + (size == 0)
    if 0x80 <= op < 0xA0:
        return 4
    if 0xA0 <= op < 0xE0:
        return 3
    return 1


class GPU:
    def __init__(self) -> None:
        self.vram = np.zeros((VRAM_H, VRAM_W), dtype=np.uint16)
        self.field = 0
        self._packet: list[int] = []
        self._need = 0
        self._upload: tuple[int, int, int, int] | None = None
        self._upload_pixels: list[int] = []
        self._warned: set[int] = set()
        self._cov_cache: dict = {}
        self._reset()

    # ------------------------------------------------------------- state

    def _reset(self) -> None:
        self.texpage = 0
        self.dither = False
        self.draw_to_display = False
        self.abr = 0
        self.tex_window = (0, 0, 0, 0)
        self.area = (0, 0, 0, 0)
        self.offset = (0, 0)
        self.mask_set = False
        self.mask_check = False
        self.display_off = True
        self.display_start = (0, 0)
        self.hrange = (0x200, 0x200 + 256 * 10)
        self.vrange = (0x10, 0x10 + 240)
        self.mode = 0
        self._packet = []
        self._need = 0
        self._upload = None

    @property
    def interlaced(self) -> bool:
        return bool(self.mode >> 5 & 1)

    # --------------------------------------------------------------- ports

    def gp1(self, word: int) -> None:
        cmd, arg = word >> 24 & 0x3F, word & 0xFFFFFF
        if cmd == 0x00:
            self._reset()
        elif cmd == 0x01:
            self._packet, self._need, self._upload = [], 0, None
        elif cmd == 0x03:
            self.display_off = bool(arg & 1)
        elif cmd == 0x05:
            self.display_start = (arg & 0x3FF, arg >> 10 & 0x1FF)
        elif cmd == 0x06:
            self.hrange = (arg & 0xFFF, arg >> 12 & 0xFFF)
        elif cmd == 0x07:
            self.vrange = (arg & 0x3FF, arg >> 10 & 0x3FF)
        elif cmd == 0x08:
            self.mode = arg & 0xFF

    def gp0(self, word: int) -> None:
        if self._upload is not None:
            self._upload_pixels.append(word & 0xFFFF)
            self._upload_pixels.append(word >> 16)
            x, y, w, h = self._upload
            if len(self._upload_pixels) >= w * h:
                self._finish_upload()
            return

        self._packet.append(word)
        if len(self._packet) == 1:
            self._need = _packet_length(word >> 24)
        if len(self._packet) < self._need:
            return

        packet, self._packet = self._packet, []
        self._execute(packet)

    # ------------------------------------------------------------ commands

    def _execute(self, p: list[int]) -> None:
        op = p[0] >> 24
        if op == 0x02:
            self._fill(p)
        elif 0x20 <= op < 0x40:
            self._polygon(p)
        elif 0x80 <= op < 0xA0:
            self._copy(p)
        elif 0xA0 <= op < 0xC0:
            self._start_upload(p)
        elif op == 0xE1:
            self._set_texpage(p[0])
            self.dither = bool(p[0] >> 9 & 1)
            self.draw_to_display = bool(p[0] >> 10 & 1)
        elif op == 0xE2:
            w = p[0]
            self.tex_window = (w & 0x1F, w >> 5 & 0x1F, w >> 10 & 0x1F, w >> 15 & 0x1F)
        elif op == 0xE3:
            self.area = (p[0] & 0x3FF, p[0] >> 10 & 0x1FF, self.area[2], self.area[3])
        elif op == 0xE4:
            self.area = (self.area[0], self.area[1], p[0] & 0x3FF, p[0] >> 10 & 0x1FF)
        elif op == 0xE5:
            self.offset = (_sign11(p[0]), _sign11(p[0] >> 11))
        elif op == 0xE6:
            self.mask_set, self.mask_check = bool(p[0] & 1), bool(p[0] & 2)
        elif op in (0x00, 0x01) or 0xC0 <= op < 0xE0 or 0x03 <= op < 0x20 or op >= 0xE7:
            pass  # NOP / cache flush / VRAM->CPU (nobody reads it back here)
        elif op not in self._warned:
            self._warned.add(op)
            log.warning("Comando GP0 %02xh não suportado — ignorado", op)

    def _set_texpage(self, bits: int) -> None:
        self.texpage = bits & 0x9FF
        self.abr = bits >> 5 & 3

    def _skip_rows(self, ys: np.ndarray) -> np.ndarray:
        """Rows the GPU won't touch: in 480i with drawing to the displayed
        area prohibited, the lines of the field being shown are skipped."""
        if self.interlaced and self.mode >> 2 & 1 and not self.draw_to_display:
            return (ys & 1) == self.field
        return np.zeros(ys.shape, dtype=bool)

    def _fill(self, p: list[int]) -> None:
        color = rgb15(p[0] & 0xFF, p[0] >> 8 & 0xFF, p[0] >> 16 & 0xFF)
        x, y = p[1] & 0x3F0, p[1] >> 16 & 0x1FF
        w, h = ((p[2] & 0x3FF) + 0xF) & ~0xF, p[2] >> 16 & 0x1FF
        ys = (np.arange(y, y + h) & 0x1FF)
        ys = ys[~self._skip_rows(ys)]
        xs = np.arange(x, x + w) & 0x3FF
        self.vram[np.ix_(ys, xs)] = color

    def _copy(self, p: list[int]) -> None:
        sx, sy = p[1] & 0x3FF, p[1] >> 16 & 0x1FF
        dx, dy = p[2] & 0x3FF, p[2] >> 16 & 0x1FF
        w = ((p[3] & 0xFFFF) - 1 & 0x3FF) + 1
        h = ((p[3] >> 16) - 1 & 0x1FF) + 1
        src = self.vram[np.ix_(np.arange(sy, sy + h) & 0x1FF, np.arange(sx, sx + w) & 0x3FF)].copy()
        self._write_block(dx, dy, w, h, src)

    def _start_upload(self, p: list[int]) -> None:
        x, y = p[1] & 0x3FF, p[1] >> 16 & 0x1FF
        w = ((p[2] & 0xFFFF) - 1 & 0x3FF) + 1
        h = ((p[2] >> 16) - 1 & 0x1FF) + 1
        self._upload = (x, y, w, h)
        self._upload_pixels = []

    def _finish_upload(self) -> None:
        x, y, w, h = self._upload
        pixels = np.array(self._upload_pixels[: w * h], dtype=np.uint16).reshape(h, w)
        self._upload, self._upload_pixels = None, []
        self._write_block(x, y, w, h, pixels)

    def _write_block(self, x: int, y: int, w: int, h: int, pixels: np.ndarray) -> None:
        ys = np.arange(y, y + h) & 0x1FF
        xs = np.arange(x, x + w) & 0x3FF
        dst = self.vram[np.ix_(ys, xs)]
        out = pixels | (0x8000 if self.mask_set else 0)
        if self.mask_check:
            out = np.where(dst & 0x8000, dst, out)
        self.vram[np.ix_(ys, xs)] = out

    # ------------------------------------------------------------ polygons

    def _polygon(self, p: list[int]) -> None:
        op = p[0] >> 24
        gouraud, quad = op >> 4 & 1, op >> 3 & 1
        textured, semi, raw = op >> 2 & 1, op >> 1 & 1, op & 1

        verts, colors, uvs = [], [], []
        i = 1
        for n in range(4 if quad else 3):
            if gouraud and n > 0:
                color = p[i]
                i += 1
            elif gouraud or n == 0:
                color = p[0]
            colors.append((color & 0xFF, color >> 8 & 0xFF, color >> 16 & 0xFF))
            xy = p[i]
            i += 1
            verts.append((_sign11(xy) + self.offset[0], _sign11(xy >> 16) + self.offset[1]))
            if textured:
                uvs.append((p[i] & 0xFF, p[i] >> 8 & 0xFF))
                if n == 0:
                    clut = p[i] >> 16
                elif n == 1:
                    self._set_texpage((self.texpage & ~0x9FF) | (p[i] >> 16 & 0x9FF))
                i += 1
            else:
                uvs.append((0, 0))

        if quad and not textured and not semi and self._is_flat(colors, gouraud) \
                and self._axis_aligned(verts):
            self._fill_rect(verts, colors[0])
            return

        tex = None
        if textured:
            tex = ((clut & 0x3F) * 16, clut >> 6 & 0x1FF)

        style = dict(gouraud=bool(gouraud), textured=bool(textured), semi=bool(semi),
                     raw=bool(raw), clut=tex)
        self._triangle(verts[0:3], colors[0:3], uvs[0:3], **style)
        if quad:
            self._triangle(verts[1:4], colors[1:4], uvs[1:4], **style)

    def _is_flat(self, colors, gouraud: bool) -> bool:
        # a gouraud quad with equal colors is still dithered, so it isn't "flat"
        return not gouraud or (not self.dither and len(set(colors)) == 1)

    @staticmethod
    def _axis_aligned(v) -> bool:
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = v
        return x0 == x2 and x1 == x3 and y0 == y1 and y2 == y3 and x1 > x0 and y2 > y0

    def _fill_rect(self, v, color) -> None:
        """Same pixels the two triangles of an axis-aligned quad would cover."""
        (x0, y0), (x1, _), (_, y2), _ = v
        if x1 - x0 >= 1024 or y2 - y0 >= 512:
            return
        ax1, ay1, ax2, ay2 = self.area
        xa, xb = max(x0, ax1), min(x1 - 1, ax2)
        ya, yb = max(y0, ay1), min(y2 - 1, ay2)
        if xa > xb or ya > yb:
            return
        pixel = rgb15(*color) | (0x8000 if self.mask_set else 0)
        rows = np.arange(ya, yb + 1)
        rows = rows[~self._skip_rows(rows)]
        region = self.vram[rows, xa:xb + 1]
        if self.mask_check:
            region = np.where(region & 0x8000, region, pixel)
            self.vram[rows, xa:xb + 1] = region
        else:
            self.vram[rows, xa:xb + 1] = pixel

    def _coverage(self, v):
        """Pixels inside a (counter-clockwise ordered) triangle, cached by geometry."""
        skip = self.interlaced and self.mode >> 2 & 1 and not self.draw_to_display
        key = (tuple(v), self.area, self.field if skip else -1)
        if key in self._cov_cache:
            return self._cov_cache[key]
        xs_ = [p[0] for p in v]
        ys_ = [p[1] for p in v]
        ax1, ay1, ax2, ay2 = self.area
        minx, maxx = max(min(xs_), ax1), min(max(xs_) - 1, ax2)
        miny, maxy = max(min(ys_), ay1), min(max(ys_) - 1, ay2)
        result = None
        if minx <= maxx and miny <= maxy:
            py, px = np.mgrid[miny:maxy + 1, minx:maxx + 1]
            px = px.astype(np.int64)
            py = py.astype(np.int64)
            inside = np.ones(px.shape, dtype=bool)
            for (axx, ayy), (bx, by) in ((v[0], v[1]), (v[1], v[2]), (v[2], v[0])):
                w = (bx - axx) * (py - ayy) - (by - ayy) * (px - axx)
                top_left = by < ayy or (by == ayy and bx > axx)
                inside &= (w > 0) | ((w == 0) & top_left)
            inside &= ~self._skip_rows(py)
            if inside.any():
                result = (px[inside], py[inside])
        if len(self._cov_cache) > 4096:
            self._cov_cache.clear()
        self._cov_cache[key] = result
        return result

    def _triangle(self, v, c, uv, *, gouraud, textured, semi, raw, clut) -> None:
        (x0, y0), (x1, y1), (x2, y2) = v
        xs_ = (x0, x1, x2)
        ys_ = (y0, y1, y2)
        if max(xs_) - min(xs_) >= 1024 or max(ys_) - min(ys_) >= 512:
            return
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if area == 0:
            return
        if area < 0:
            v = [v[0], v[2], v[1]]
            c = [c[0], c[2], c[1]]
            uv = [uv[0], uv[2], uv[1]]
            (x0, y0), (x1, y1), (x2, y2) = v
            area = -area

        cov = self._coverage(v)
        if cov is None:
            return
        px, py = cov
        dx, dy = px - x0, py - y0

        def interp(a0, a1, a2):
            nx = (a1 - a0) * (y2 - y0) - (a2 - a0) * (y1 - y0)
            ny = (a2 - a0) * (x1 - x0) - (a1 - a0) * (x2 - x0)
            return a0 + np.floor_divide(2 * (nx * dx + ny * dy) + area, 2 * area)

        if gouraud:
            rgb = [interp(c[0][k], c[1][k], c[2][k]) for k in range(3)]
        else:
            rgb = [np.full(px.shape, c[0][k], dtype=np.int64) for k in range(3)]

        dith = self.dither and (gouraud or (textured and not raw))
        offs = DITHER[py & 3, px & 3] if dith else 0

        bit15 = np.zeros(px.shape, dtype=np.int64)
        apply_semi = np.full(px.shape, bool(semi))

        if textured:
            u = interp(uv[0][0], uv[1][0], uv[2][0]) & 0xFF
            vv = interp(uv[0][1], uv[1][1], uv[2][1]) & 0xFF
            texel = self._fetch_texels(u, vv, clut).astype(np.int64)
            opaque = texel != 0
            if not opaque.any():
                return
            px, py, texel, offs = px[opaque], py[opaque], texel[opaque], (
                offs[opaque] if dith else 0)
            rgb = [ch[opaque] for ch in rgb]
            bit15 = texel >> 15 & 1
            apply_semi = semi & (bit15 == 1)
            t5 = [texel & 0x1F, texel >> 5 & 0x1F, texel >> 10 & 0x1F]
            if raw:
                out5 = t5
            else:
                out5 = [self._to5((t5[k] * rgb[k]) >> 4, offs, dith) for k in range(3)]
        else:
            out5 = [self._to5(rgb[k], offs, dith) for k in range(3)]

        if np.any(apply_semi):
            bg = self.vram[py, px].astype(np.int64)
            b5 = [bg & 0x1F, bg >> 5 & 0x1F, bg >> 10 & 0x1F]
            out5 = [np.where(apply_semi, self._blend(b5[k], out5[k]), out5[k]) for k in range(3)]

        pixel = out5[0] | out5[1] << 5 | out5[2] << 10 | bit15 << 15
        if self.mask_set:
            pixel |= 0x8000
        if self.mask_check:
            keep = (self.vram[py, px] & 0x8000) == 0
            px, py, pixel = px[keep], py[keep], pixel[keep]
        self.vram[py, px] = pixel.astype(np.uint16)

    @staticmethod
    def _to5(c8: np.ndarray, offs, dith: bool) -> np.ndarray:
        if dith:
            return np.clip(c8 + offs, 0, 255) >> 3
        return np.minimum(c8, 255) >> 3

    def _blend(self, b: np.ndarray, f: np.ndarray) -> np.ndarray:
        if self.abr == 0:
            return (b + f) >> 1
        if self.abr == 1:
            return np.minimum(b + f, 31)
        if self.abr == 2:
            return np.maximum(b - f, 0)
        return np.minimum(b + (f >> 2), 31)

    def _fetch_texels(self, u: np.ndarray, v: np.ndarray, clut) -> np.ndarray:
        mx, my, ox, oy = self.tex_window
        u = (u & ~(mx * 8)) | ((ox & mx) * 8)
        v = (v & ~(my * 8)) | ((oy & my) * 8)
        tpx = (self.texpage & 0xF) * 64
        tpy = (self.texpage >> 4 & 1) * 256
        depth = self.texpage >> 7 & 3
        rows = (tpy + v) & 0x1FF
        if depth == 0:
            hw = self.vram[rows, (tpx + (u >> 2)) & 0x3FF].astype(np.int64)
            idx = hw >> ((u & 3) * 4) & 0xF
        elif depth == 1:
            hw = self.vram[rows, (tpx + (u >> 1)) & 0x3FF].astype(np.int64)
            idx = hw >> ((u & 1) * 8) & 0xFF
        else:
            return self.vram[rows, (tpx + u) & 0x3FF]
        cx, cy = clut
        return self.vram[cy, (cx + idx) & 0x3FF]

    # ------------------------------------------------------------- display

    def display_size(self) -> tuple[int, int]:
        div = 7 if self.mode >> 6 & 1 else _DOTCLOCK_DIV[self.mode & 3]
        w = (self.hrange[1] - self.hrange[0]) // div
        w = (w + 2) & ~3  # the GPU rounds the pixel count to a multiple of 4
        h = self.vrange[1] - self.vrange[0]
        if self.interlaced and self.mode >> 2 & 1:
            h *= 2
        return max(w, 0), max(h, 0)

    def frame(self):
        """What replay.video yields for a changed field: the displayed image."""
        return self.display_rgb()

    def display_rgb(self) -> np.ndarray:
        w, h = self.display_size()
        if self.display_off or w == 0 or h == 0:
            return np.zeros((max(h, 1), max(w, 1), 3), dtype=np.uint8)
        x, y = self.display_start
        return rgb8(self.vram[np.ix_(np.arange(y, y + h) & 0x1FF, np.arange(x, x + w) & 0x3FF)])
