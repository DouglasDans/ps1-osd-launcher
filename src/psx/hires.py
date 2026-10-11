"""Enhanced renderer: the same GPU command stream, drawn the way an upscaling
emulator (DuckStation's hardware renderer) draws it.

HiResGPU is a GPU whose native 15-bit VRAM keeps working as on the console
(textures, CLUTs and uploads are read from it), while every primitive is also
drawn into a high-resolution RGB copy of the VRAM:

- vertices are scaled by (sx, sy), so edges are as sharp as the output allows;
- progressive: 480i field line skipping doesn't apply;
- true color: 24-bit colors and gouraud shading, no 15-bit reduction, no dither;
- textures can be filtered (JINC2) instead of sampled nearest, or replaced
  by HD versions (src.psx.textures), sampled bilinearly.

Pixels are sampled at their centers in native coordinates: at scale 1 the
coverage of axis-aligned shapes is the same as the console's.
"""

import math

import numpy as np

from src.psx.gpu import GPU, VRAM_H, VRAM_W, rgb8
from src.psx.textures import texture_key, texture_rect

FILTERS = ("nearest", "jinc2")

# JINC2: Hyllian's windowed-jinc resampler as in DuckStation's gpu_hw_shadergen.cpp
# (4x4 taps, anti-ringing clamp to the central 2x2, coverage alpha filtered
# alongside and divided out: transparent texels contribute no color)
_JINC2_WINDOW_SINC = 0.44
_JINC2_SINC = 0.82
_JINC2_AR_STRENGTH = 0.8
_JINC2_OVERSAMPLE = 4  # filtered texture grid: samples per texel and axis


def _jinc2_weight(d: np.ndarray) -> np.ndarray:
    wa, wb = _JINC2_WINDOW_SINC * math.pi, _JINC2_SINC * math.pi
    d = np.asarray(d, dtype=np.float64)
    safe = np.where(d == 0, 1.0, d)
    return np.where(d == 0, wa * wb, np.sin(safe * wa) * np.sin(safe * wb) / (safe * safe))


def jinc2(fetch, u: np.ndarray, v: np.ndarray):
    """Filters texels around continuous texture coordinates (texel i spans
    [i, i+1)). fetch(iu, iv) -> (rgb float (n, 3), alpha float (n,)).
    Color is filtered premultiplied by alpha, so transparent texels don't
    bleed their (meaningless) color into the edges."""
    bu, bv = np.floor(u - 0.5).astype(np.int64), np.floor(v - 0.5).astype(np.int64)
    acc = np.zeros((u.size, 4))
    wsum = np.zeros(u.size)
    near = []
    for j in range(-1, 3):
        for i in range(-1, 3):
            tu, tv = bu + i, bv + j
            w = _jinc2_weight(np.hypot(tu + 0.5 - u, tv + 0.5 - v))
            rgb, a = fetch(tu, tv)
            px = np.concatenate([rgb * a[:, None], a[:, None]], axis=1)
            acc += px * w[:, None]
            wsum += w
            if i in (0, 1) and j in (0, 1):
                near.append(px)
    out = acc / wsum[:, None]
    lo, hi = np.minimum.reduce(near), np.maximum.reduce(near)
    out = out + (np.clip(out, lo, hi) - out) * _JINC2_AR_STRENGTH
    # quantized to 8 bits like a GPU texture; also keeps flat areas exact
    alpha = np.rint(np.clip(out[:, 3], 0.0, 1.0) * 255) / 255
    rgb = np.where(alpha[:, None] > 0, out[:, :3] / np.maximum(alpha, 1e-6)[:, None], 0.0)
    return np.rint(np.clip(rgb, 0.0, 255.0)), alpha


class HiResGPU(GPU):
    def __init__(self, scale: tuple[float, float] = (3, 2.25), texture_filter: str = "jinc2",
                 replacements: dict[str, np.ndarray] | None = None) -> None:
        if texture_filter not in FILTERS:
            raise ValueError(f"unknown texture filter {texture_filter!r} (use one of {FILTERS})")
        self.sx, self.sy = float(scale[0]), float(scale[1])
        self.texture_filter = texture_filter
        self.hires = np.zeros((round(VRAM_H * self.sy), round(VRAM_W * self.sx), 3), dtype=np.uint8)
        self._copying = False
        self._tex_cache: dict = {}
        self._warm_only = False  # see prepare()
        self.replacements = replacements or {}
        self._key_cache: dict = {}
        self._sprite_cache: dict = {}
        super().__init__()

    # ------------------------------------------------------- coordinates

    def _cols(self, x0: float, x1: float) -> tuple[int, int]:
        """Hi-res columns whose centers fall in native [x0, x1)."""
        w = self.hires.shape[1]
        return (min(max(math.ceil(x0 * self.sx - 0.5), 0), w),
                min(max(math.ceil(x1 * self.sx - 0.5), 0), w))

    def _rows(self, y0: float, y1: float) -> tuple[int, int]:
        h = self.hires.shape[0]
        return (min(max(math.ceil(y0 * self.sy - 0.5), 0), h),
                min(max(math.ceil(y1 * self.sy - 0.5), 0), h))

    def _paint(self, X0: int, X1: int, Y0: int, Y1: int, color) -> None:
        if self._warm_only:
            return
        # assigning a ready-made row broadcasts much faster than a 3-tuple
        row = np.empty((X1 - X0, 3), dtype=np.uint8)
        row[:] = color
        self.hires[Y0:Y1, X0:X1] = row

    def _area_bounds(self) -> tuple[int, int, int, int]:
        ax1, ay1, ax2, ay2 = self.area
        return (*self._cols(ax1, ax2 + 1), *self._rows(ay1, ay2 + 1))

    # ---------------------------------------------------- vram transfers

    def _fill(self, p: list[int]) -> None:
        super()._fill(p)
        x, y = p[1] & 0x3F0, p[1] >> 16 & 0x1FF
        w, h = ((p[2] & 0x3FF) + 0xF) & ~0xF, p[2] >> 16 & 0x1FF
        X0, X1 = self._cols(x, x + w)
        Y0, Y1 = self._rows(y, y + h)
        if X0 < X1 and Y0 < Y1:
            self._paint(X0, X1, Y0, Y1, (p[0] & 0xFF, p[0] >> 8 & 0xFF, p[0] >> 16 & 0xFF))

    def _write_block(self, x: int, y: int, w: int, h: int, pixels: np.ndarray) -> None:
        super()._write_block(x, y, w, h, pixels)
        if self._copying or self._warm_only:
            return
        X0, X1 = self._cols(x, x + w)
        Y0, Y1 = self._rows(y, y + h)
        if X0 >= X1 or Y0 >= Y1:
            return
        cols = (np.floor((np.arange(X0, X1) + 0.5) / self.sx).astype(np.int64)) & 0x3FF
        rows = (np.floor((np.arange(Y0, Y1) + 0.5) / self.sy).astype(np.int64)) & 0x1FF
        self.hires[Y0:Y1, X0:X1] = rgb8(self.vram[np.ix_(rows, cols)])

    def _copy(self, p: list[int]) -> None:
        self._copying = True
        try:
            super()._copy(p)
        finally:
            self._copying = False
        if self._warm_only:
            return
        sx, sy = p[1] & 0x3FF, p[1] >> 16 & 0x1FF
        dx, dy = p[2] & 0x3FF, p[2] >> 16 & 0x1FF
        w = ((p[3] & 0xFFFF) - 1 & 0x3FF) + 1
        h = ((p[3] >> 16) - 1 & 0x1FF) + 1
        SX0, SX1 = self._cols(sx, sx + w)
        SY0, SY1 = self._rows(sy, sy + h)
        DX0, DX1 = self._cols(dx, dx + w)
        DY0, DY1 = self._rows(dy, dy + h)
        cw, ch = min(SX1 - SX0, DX1 - DX0), min(SY1 - SY0, DY1 - DY0)
        if cw > 0 and ch > 0:
            self.hires[DY0:DY0 + ch, DX0:DX0 + cw] = self.hires[SY0:SY0 + ch, SX0:SX0 + cw].copy()

    # ------------------------------------------------------- primitives

    def _fill_rect(self, v, color) -> None:
        super()._fill_rect(v, color)
        (x0, y0), (x1, _), (_, y2), _ = v
        if x1 - x0 >= 1024 or y2 - y0 >= 512:
            return
        X0, X1 = self._cols(x0, x1)
        Y0, Y1 = self._rows(y0, y2)
        AX0, AX1, AY0, AY1 = self._area_bounds()
        X0, X1, Y0, Y1 = max(X0, AX0), min(X1, AX1), max(Y0, AY0), min(Y1, AY1)
        if X0 < X1 and Y0 < Y1:
            self._paint(X0, X1, Y0, Y1, color)

    def _triangle(self, v, c, uv, *, gouraud, textured, semi, raw, clut) -> None:
        super()._triangle(v, c, uv, gouraud=gouraud, textured=textured, semi=semi, raw=raw, clut=clut)
        if self._warm_only:
            if textured and self._replacement(uv, clut) is None and self.texture_filter != "nearest":
                self._filtered_texture(uv, clut)
            return

        xs_, ys_ = [p[0] for p in v], [p[1] for p in v]
        if max(xs_) - min(xs_) >= 1024 or max(ys_) - min(ys_) >= 512:
            return
        (x0, y0), (x1, y1), (x2, y2) = v
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if area == 0:
            return
        if area < 0:
            v, c, uv = [v[0], v[2], v[1]], [c[0], c[2], c[1]], [uv[0], uv[2], uv[1]]
            (x0, y0), (x1, y1), (x2, y2) = v
            area = -area

        X0, X1 = self._cols(min(xs_), max(xs_))
        Y0, Y1 = self._rows(min(ys_), max(ys_))
        AX0, AX1, AY0, AY1 = self._area_bounds()
        X0, X1, Y0, Y1 = max(X0, AX0), min(X1, AX1), max(Y0, AY0), min(Y1, AY1)
        if X0 >= X1 or Y0 >= Y1:
            return

        xc = (np.arange(X0, X1) + 0.5) / self.sx
        yc = (np.arange(Y0, Y1) + 0.5) / self.sy

        def coverage():
            inside = np.ones((Y1 - Y0, X1 - X0), dtype=bool)
            for (ax, ay), (bx, by) in ((v[0], v[1]), (v[1], v[2]), (v[2], v[0])):
                w = (bx - ax) * (yc - ay)[:, None] - (by - ay) * (xc - ax)[None, :]
                top_left = by < ay or (by == ay and bx > ax)
                inside &= (w > 0) | ((w == 0) & top_left) if top_left else (w > 0)
            return inside

        def gradient(a0, a1, a2):
            nx = (a1 - a0) * (y2 - y0) - (a2 - a0) * (y1 - y0)
            ny = (a2 - a0) * (x1 - x0) - (a1 - a0) * (x2 - x0)
            return nx / area, ny / area

        if not textured and not semi:
            # untextured opaque (the background and the diamond): whole-bbox
            # planes written through the coverage mask, one channel at a time
            region = self.hires[Y0:Y1, X0:X1]
            inside = coverage()
            fx, fy = (xc - x0).astype(np.float32), (yc - y0).astype(np.float32)
            for k in range(3):
                if gouraud:
                    gx, gy = gradient(c[0][k], c[1][k], c[2][k])
                    plane = (c[0][k] + np.float32(gx) * fx)[None, :] + (np.float32(gy) * fy)[:, None]
                    np.copyto(region[..., k], plane, where=inside, casting="unsafe")
                else:
                    np.copyto(region[..., k], c[0][k], where=inside, casting="unsafe")
            return

        # covered pixels and sampled texels depend only on the geometry and
        # the texture: static sprites (the logo text) are sampled once
        key = None
        if textured:
            key = (tuple(v), tuple(uv), clut, self.texpage, self.tex_window, (X0, X1, Y0, Y1),
                   self._texture_digest(clut))
        hit = self._sprite_cache.get(key) if key else None
        if hit:
            iy, ix, dx, dy, sampled = hit
        else:
            iy, ix = np.nonzero(coverage())
            if iy.size == 0:
                return
            dx, dy = xc[ix] - x0, yc[iy] - y0
            sampled = None

        def interp(a0, a1, a2):
            gx, gy = gradient(a0, a1, a2)
            return a0 + gx * dx + gy * dy

        if gouraud:
            col = np.stack([interp(c[0][k], c[1][k], c[2][k]) for k in range(3)], axis=1)
        else:
            col = np.broadcast_to(np.array(c[0], dtype=np.float64), (iy.size, 3))

        Y, X = iy + Y0, ix + X0
        alpha = None
        stp = None
        if textured:
            if sampled is None:
                u = interp(uv[0][0], uv[1][0], uv[2][0])
                tv = interp(uv[0][1], uv[1][1], uv[2][1])
                sampled = self._sample(u, tv, uv, clut)
                if len(self._sprite_cache) > 64:
                    self._sprite_cache.clear()
                self._sprite_cache[key] = (iy, ix, dx, dy, sampled)
            rgb, alpha, stp = sampled
            out = rgb if raw else rgb * col / 128.0
        else:
            out = col

        dst = self.hires[Y, X].astype(np.float64)
        if semi:
            blended = self._blend8(dst, out)
            out = blended if stp is None else np.where(stp[:, None], blended, out)
        if alpha is not None:
            out = out * alpha[:, None] + dst * (1.0 - alpha[:, None])
        self.hires[Y, X] = np.clip(np.floor(out), 0, 255).astype(np.uint8)

    def _blend8(self, b: np.ndarray, f: np.ndarray) -> np.ndarray:
        if self.abr == 0:
            return (b + f) / 2
        if self.abr == 1:
            return b + f
        if self.abr == 2:
            return b - f
        return b + f / 4

    # ---------------------------------------------------------- textures

    def _texels(self, iu: np.ndarray, iv: np.ndarray, clut):
        """Integer texel coordinates -> (rgb float (n, 3), alpha, stp bit)."""
        texel = self._fetch_texels(iu & 0xFF, iv & 0xFF, clut).astype(np.int64)
        return rgb8(texel).astype(np.float64), (texel != 0).astype(np.float64), (texel >> 15 & 1) == 1

    def _sample(self, u: np.ndarray, v: np.ndarray, uv, clut):
        hd = self._replacement(uv, clut)
        if hd is not None:
            return self._bilinear(hd, u, v, uv)
        if self.texture_filter == "nearest":
            return self._texels(np.floor(u).astype(np.int64), np.floor(v).astype(np.int64), clut)

        img, a_img, stp_img, (u0, v0) = self._filtered_texture(uv, clut)
        f = _JINC2_OVERSAMPLE
        gx = np.clip(np.floor((u - u0) * f).astype(np.int64), 0, img.shape[1] - 1)
        gy = np.clip(np.floor((v - v0) * f).astype(np.int64), 0, img.shape[0] - 1)
        return img[gy, gx], a_img[gy, gx], stp_img[gy, gx]

    def _replacement(self, uv, clut) -> np.ndarray | None:
        """The HD version of the texels this primitive reads, if there is one."""
        if not self.replacements:
            return None
        u0, u1 = min(t[0] for t in uv), max(t[0] for t in uv)
        v0, v1 = min(t[1] for t in uv), max(t[1] for t in uv)
        cache_key = (self.texpage, clut, self.tex_window, u0, u1, v0, v1, self._texture_digest(clut))
        key = self._key_cache.get(cache_key)
        if key is None:
            key = texture_key(texture_rect(self, uv, clut))
            if len(self._key_cache) > 256:
                self._key_cache.clear()
            self._key_cache[cache_key] = key
        return self.replacements.get(key)

    @staticmethod
    def _bilinear(hd: np.ndarray, u: np.ndarray, v: np.ndarray, uv):
        """Samples an HD texture covering texels [u0, u1] x [v0, v1]."""
        u0, u1 = min(t[0] for t in uv), max(t[0] for t in uv)
        v0, v1 = min(t[1] for t in uv), max(t[1] for t in uv)
        h, w = hd.shape[:2]
        x = np.clip((u - u0) * (w / (u1 + 1 - u0)) - 0.5, 0, w - 1)
        y = np.clip((v - v0) * (h / (v1 + 1 - v0)) - 0.5, 0, h - 1)
        x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
        x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
        fx, fy = (x - x0)[:, None], (y - y0)[:, None]
        px = ((hd[y0, x0] * (1 - fx) + hd[y0, x1] * fx) * (1 - fy)
              + (hd[y1, x0] * (1 - fx) + hd[y1, x1] * fx) * fy)
        return px[:, :3], px[:, 3], None

    def _filtered_texture(self, uv, clut):
        """The texels a primitive with these vertex UVs can read, JINC2-filtered
        on a grid of _JINC2_OVERSAMPLE samples per texel. Cached while the
        texture page, CLUT and their VRAM contents stay the same."""
        u0, u1 = min(t[0] for t in uv), max(t[0] for t in uv) + 1
        v0, v1 = min(t[1] for t in uv), max(t[1] for t in uv) + 1
        key = (self.texpage, clut, self.tex_window, u0, u1, v0, v1, self._texture_digest(clut))
        hit = self._tex_cache.get(key)
        if hit is not None:
            return hit

        f = _JINC2_OVERSAMPLE
        gu = u0 + (np.arange((u1 - u0) * f) + 0.5) / f
        gv = v0 + (np.arange((v1 - v0) * f) + 0.5) / f
        GU, GV = np.meshgrid(gu, gv)

        def fetch(iu, iv):
            rgb, a, _ = self._texels(iu, iv, clut)
            return rgb, a

        rgb, alpha = jinc2(fetch, GU.ravel(), GV.ravel())
        _, _, stp = self._texels(np.floor(GU.ravel()).astype(np.int64), np.floor(GV.ravel()).astype(np.int64), clut)
        shape = GU.shape
        result = (rgb.reshape(*shape, 3), alpha.reshape(shape), stp.reshape(shape), (u0, v0))
        if len(self._tex_cache) > 64:
            self._tex_cache.clear()
        self._tex_cache[key] = result
        return result

    def _texture_digest(self, clut) -> int:
        tpx = (self.texpage & 0xF) * 64
        tpy = (self.texpage >> 4 & 1) * 256
        page = self.vram[tpy:tpy + 256, tpx:tpx + 256]
        digest = hash(page.tobytes())
        if clut is not None:
            cx, cy = clut
            digest ^= hash(self.vram[cy, cx:cx + 256].tobytes())
        return digest

    # ----------------------------------------------------------- warm-up

    def prepare(self, trace) -> None:
        """Filters, ahead of time, every texture the trace draws that has no
        HD replacement: JINC2 is far too slow to run the first time a sprite
        shows up on screen. Runs the trace through a copy that only updates
        VRAM and fills the caches."""
        if self.texture_filter == "nearest":
            return
        warmer = HiResGPU(scale=(self.sx, self.sy), texture_filter=self.texture_filter,
                          replacements=self.replacements)
        warmer._warm_only = True
        warmer._tex_cache = self._tex_cache
        warmer._key_cache = self._key_cache
        for i, f in enumerate(trace.fields):
            warmer.field = i & 1
            for port, word in f.gpu:
                if port:
                    warmer.gp1(word)
                else:
                    warmer.gp0(word)

    # ----------------------------------------------------------- display

    def display_rgb(self) -> np.ndarray:
        w, h = self.display_size()
        ow, oh = round(w * self.sx), round(h * self.sy)
        if self.display_off or w == 0 or h == 0:
            return np.zeros((max(oh, 1), max(ow, 1), 3), dtype=np.uint8)
        x, y = self.display_start
        X0, Y0 = round(x * self.sx), round(y * self.sy)
        img = self.hires[Y0:Y0 + oh, X0:X0 + ow]
        if img.shape[:2] != (oh, ow):  # display area runs past the VRAM edge
            out = np.zeros((oh, ow, 3), dtype=np.uint8)
            out[:img.shape[0], :img.shape[1]] = img
            return out
        return img.copy()
