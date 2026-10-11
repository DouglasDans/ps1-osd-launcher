"""GPU renderer: the GPU command stream drawn by the graphics card, through
SDL's renderer (SDL_RenderGeometry, OpenGL ES on the Pi), the way an
upscaling emulator's hardware renderer draws it.

RendererGPU keeps the native 15-bit VRAM working on the CPU (uploads, CLUTs,
textures), like HiResGPU, but draws every primitive into a target texture
that stands for the VRAM at the output scale: vertices are scaled, colors and
gouraud shading are interpolated by the GPU in true color, 480i field line
skipping doesn't apply, and textures are HD replacements (src.psx.textures)
or the native texels, JINC2-filtered or nearest.

Limits (none of them used by the BIOS intro): semi-transparency mode 2
(subtract) is drawn opaque, and textured semi-transparency applies to the
whole primitive instead of only to texels with the STP bit.
"""

import logging
import math
import warnings

import numpy as np
import pygame
from pygame._sdl2 import video

from src.psx.gpu import GPU, VRAM_H, VRAM_W, rgb8
from src.psx.hires import _JINC2_OVERSAMPLE, jinc2
from src.psx.textures import texture_key, texture_rect

log = logging.getLogger("ps1.psx.gpurender")

_BLEND_NONE, _BLEND_BLEND, _BLEND_ADD = 0, 1, 2  # SDL_BlendMode values


def display_flags(screen: pygame.Surface) -> int:
    """The set_mode flags to reopen this display with (only FULLSCREEN
    matters here). Masking get_flags() would give the unsigned 0x80000000,
    which set_mode rejects, so the pygame constant is returned instead."""
    return pygame.FULLSCREEN if screen.get_flags() & pygame.FULLSCREEN else 0


def open_renderer(screen: pygame.Surface) -> video.Renderer | None:
    """Turns the display into a hardware-rendered one (same size and
    fullscreen flag). Returns None, with the plain display back, if the
    platform can't do it."""
    size, flags = screen.get_size(), display_flags(screen)
    try:
        with warnings.catch_warnings():
            # "no fast renderer available" (software fallback) and the
            # deprecation of from_display_module, the only bridge from the
            # display module to a Renderer
            warnings.simplefilter("ignore")
            pygame.display.set_mode(size, flags, vsync=1)
            return video.Renderer.from_window(video.Window.from_display_module())
    except (pygame.error, ValueError) as e:
        log.warning("Renderer de GPU indisponível (%s) — desenhando na CPU", e)
        pygame.display.set_mode(size, flags)
        return None


def close_renderer(size: tuple[int, int], flags: int) -> pygame.Surface:
    """Back to a plain display surface (what the menu draws on)."""
    return pygame.display.set_mode(size, flags)


def _texture(renderer: video.Renderer, rgba: np.ndarray, linear: bool) -> video.Texture:
    h, w = rgba.shape[:2]
    surf = pygame.image.frombuffer(np.ascontiguousarray(rgba, dtype=np.uint8).tobytes(), (w, h), "RGBA")
    tex = video.Texture(renderer, (w, h), static=True, scale_quality=1 if linear else 0)
    tex.update(surf)
    tex.blend_mode = _BLEND_BLEND
    return tex


class RendererGPU(GPU):
    def __init__(self, renderer: video.Renderer, size: tuple[int, int], texture_filter: str = "jinc2",
                 replacements: dict[str, np.ndarray] | None = None) -> None:
        self.r = renderer
        self.out_size = size
        self.sx, self.sy = size[0] / 640, size[1] / 480
        self.texture_filter = texture_filter
        self.replacements = replacements or {}
        self.lazy = False  # see replay.video
        self._copying = False
        self._textures: dict = {}
        self._keys: dict = {}
        self.canvas = video.Texture(renderer, (round(VRAM_W * self.sx), round(VRAM_H * self.sy)), target=True)
        white = pygame.Surface((1, 1))
        white.fill((255, 255, 255))
        self._white = video.Texture.from_surface(renderer, white)
        self._white.blend_mode = _BLEND_NONE
        super().__init__()
        self.r.target = self.canvas
        self.r.set_viewport(None)
        self.r.draw_color = (0, 0, 0, 255)
        self.r.clear()

    # ------------------------------------------------------- coordinates

    def _rect(self, x0: float, y0: float, x1: float, y1: float) -> pygame.Rect:
        """Pixels whose centers fall in native [x0, x1) x [y0, y1)."""
        X0, X1 = math.ceil(x0 * self.sx - 0.5), math.ceil(x1 * self.sx - 0.5)
        Y0, Y1 = math.ceil(y0 * self.sy - 0.5), math.ceil(y1 * self.sy - 0.5)
        return pygame.Rect(X0, Y0, max(X1 - X0, 0), max(Y1 - Y0, 0))

    def _clip_to_area(self) -> pygame.Rect:
        ax1, ay1, ax2, ay2 = self.area
        area = self._rect(ax1, ay1, ax2 + 1, ay2 + 1)
        self.r.set_viewport(area)
        return area

    # ---------------------------------------------------- vram transfers

    def _fill(self, p: list[int]) -> None:
        super()._fill(p)
        if self.lazy:
            return
        x, y = p[1] & 0x3F0, p[1] >> 16 & 0x1FF
        w, h = ((p[2] & 0x3FF) + 0xF) & ~0xF, p[2] >> 16 & 0x1FF
        self.r.set_viewport(None)  # fills ignore the drawing area
        self.r.draw_blend_mode = _BLEND_NONE
        self.r.draw_color = (p[0] & 0xFF, p[0] >> 8 & 0xFF, p[0] >> 16 & 0xFF, 255)
        self.r.fill_rect(self._rect(x, y, x + w, y + h))

    def _write_block(self, x: int, y: int, w: int, h: int, pixels: np.ndarray) -> None:
        super()._write_block(x, y, w, h, pixels)
        if self._copying:
            return
        ys, xs = np.arange(y, y + h) & 0x1FF, np.arange(x, x + w) & 0x3FF
        rgba = np.dstack([rgb8(self.vram[np.ix_(ys, xs)]), np.full((h, w), 255, np.uint8)])
        tex = _texture(self.r, rgba, linear=False)
        tex.blend_mode = _BLEND_NONE
        self.r.set_viewport(None)
        self.r.blit(tex, self._rect(x, y, x + w, y + h))

    def _copy(self, p: list[int]) -> None:
        self._copying = True
        try:
            super()._copy(p)
        finally:
            self._copying = False
        sx, sy = p[1] & 0x3FF, p[1] >> 16 & 0x1FF
        dx, dy = p[2] & 0x3FF, p[2] >> 16 & 0x1FF
        w = ((p[3] & 0xFFFF) - 1 & 0x3FF) + 1
        h = ((p[3] >> 16) - 1 & 0x1FF) + 1
        src, dst = self._rect(sx, sy, sx + w, sy + h), self._rect(dx, dy, dx + w, dy + h)
        if not src.w or not src.h:
            return
        # a texture can't be its own source while it is the target
        tmp = video.Texture(self.r, src.size, target=True)
        tmp.blend_mode = _BLEND_NONE
        self.r.set_viewport(None)
        self.r.target = tmp
        self.r.blit(self.canvas, pygame.Rect((0, 0), src.size), src)
        self.r.target = self.canvas
        self.r.blit(tmp, dst)

    # ------------------------------------------------------- primitives

    def _fill_rect(self, v, color) -> None:
        super()._fill_rect(v, color)
        if self.lazy:
            return
        (x0, y0), (x1, _), (_, y2), _ = v
        if x1 - x0 >= 1024 or y2 - y0 >= 512:
            return
        area = self._clip_to_area()
        rect = self._rect(x0, y0, x1, y2).move(-area.x, -area.y)
        self.r.draw_blend_mode = _BLEND_NONE
        self.r.draw_color = (*color, 255)
        self.r.fill_rect(rect)

    def _triangle(self, v, c, uv, *, gouraud, textured, semi, raw, clut) -> None:
        super()._triangle(v, c, uv, gouraud=gouraud, textured=textured, semi=semi, raw=raw, clut=clut)
        if self.lazy:
            return
        xs_, ys_ = [p[0] for p in v], [p[1] for p in v]
        if max(xs_) - min(xs_) >= 1024 or max(ys_) - min(ys_) >= 512:
            return

        area = self._clip_to_area()
        pts = [(x * self.sx - area.x, y * self.sy - area.y) for x, y in v]
        colors = c if gouraud else [c[0]] * 3
        alpha, blend = 255, _BLEND_NONE
        if semi:
            alpha, blend = self._semi()

        if not textured:
            mods = [(*col, alpha) for col in colors]
            tex, uvs = self._white, [(0.5, 0.5)] * 3
            tex.blend_mode = blend
        else:
            tex, (u0, v0, tw, th) = self._texture_for(uv, clut)
            uvs = [((u - u0) / tw, (vv - v0) / th) for u, vv in uv]
            mods = [(255, 255, 255, alpha) if raw else (*(min(255, 2 * k) for k in col), alpha) for col in colors]
            tex.blend_mode = _BLEND_ADD if blend == _BLEND_ADD else _BLEND_BLEND
        tex.draw_triangle(*pts, *uvs, *mods)

    def _semi(self) -> tuple[int, int]:
        """(source alpha, blend mode) for the current semi-transparency mode."""
        if self.abr == 0:
            return 128, _BLEND_BLEND  # (B + F) / 2
        if self.abr == 1:
            return 255, _BLEND_ADD  # B + F
        if self.abr == 3:
            return 64, _BLEND_ADD  # B + F / 4
        if 2 not in self._warned:
            self._warned.add(2)
            log.warning("Semitransparência subtrativa não suportada no renderer de GPU — desenhando opaco")
        return 255, _BLEND_NONE

    # ---------------------------------------------------------- textures

    def _texture_for(self, uv, clut):
        """GPU texture for the texels a primitive reads, and its texel rect."""
        u0, u1 = min(t[0] for t in uv), max(t[0] for t in uv)
        v0, v1 = min(t[1] for t in uv), max(t[1] for t in uv)
        tpx, tpy = (self.texpage & 0xF) * 64, (self.texpage >> 4 & 1) * 256
        page = self.vram[tpy:tpy + 256, tpx:tpx + 256].tobytes()
        palette = self.vram[clut[1], clut[0]:clut[0] + 256].tobytes() if clut else b""
        ckey = (self.texpage, clut, self.tex_window, u0, u1, v0, v1, hash(page), hash(palette))
        key = self._keys.get(ckey)
        if key is None:
            rect = texture_rect(self, uv, clut)
            key = texture_key(rect)
            self._keys[ckey] = key
            if key not in self._textures:
                self._textures[key] = self._make_texture(key, rect)
        return self._textures[key], (u0, v0, u1 + 1 - u0, v1 + 1 - v0)

    def _make_texture(self, key: str, rect: np.ndarray) -> video.Texture:
        hd = self.replacements.get(key)
        if hd is not None:
            rgba = np.dstack([hd[..., :3], hd[..., 3] * 255.0])
            return _texture(self.r, np.clip(np.rint(rgba), 0, 255).astype(np.uint8), linear=True)
        if self.texture_filter == "nearest":
            return _texture(self.r, rect, linear=False)
        return _texture(self.r, _jinc2_upscale(rect), linear=True)

    # ----------------------------------------------------------- warm-up

    def prepare(self, trace) -> None:
        """Creates the GPU textures of everything the trace draws ahead of
        time (filtering and uploading them as a sprite first shows up would
        stall the animation)."""
        from src.psx.textures import collect

        for key, rect in collect(trace).items():
            if key not in self._textures:
                self._textures[key] = self._make_texture(key, rect)

    # ----------------------------------------------------------- display

    def _display_rect(self) -> pygame.Rect:
        w, h = self.display_size()
        x, y = self.display_start
        return pygame.Rect(round(x * self.sx), round(y * self.sy), round(w * self.sx), round(h * self.sy))

    def frame(self) -> object:
        """A token for the current picture; show() puts it on screen."""
        return object()

    def show(self, _token, screen: pygame.Surface | None = None) -> None:
        r = self.r
        r.target = None
        r.set_viewport(None)
        r.draw_color = (0, 0, 0, 255)
        r.clear()
        if not self.display_off:
            self.canvas.blend_mode = _BLEND_NONE
            r.blit(self.canvas, pygame.Rect((0, 0), self.out_size), self._display_rect())
        r.present()
        r.target = self.canvas

    def read_display(self) -> np.ndarray:
        """The displayed area as an RGB array (reads back from the GPU: slow)."""
        rect = self._display_rect()
        self.r.target = self.canvas
        self.r.set_viewport(None)
        surf = self.r.to_surface(None, rect)
        w, h = surf.get_size()
        return np.frombuffer(pygame.image.tobytes(surf, "RGB"), dtype=np.uint8).reshape(h, w, 3).copy()


def _jinc2_upscale(rect: np.ndarray) -> np.ndarray:
    """Native RGBA texels -> JINC2-filtered RGBA, _JINC2_OVERSAMPLE x larger."""
    h, w = rect.shape[:2]
    f = _JINC2_OVERSAMPLE
    rgb, a = rect[..., :3].astype(np.float64), rect[..., 3] / 255.0

    def fetch(iu, iv):
        inside = (iu >= 0) & (iu < w) & (iv >= 0) & (iv < h)
        cu, cv = np.clip(iu, 0, w - 1), np.clip(iv, 0, h - 1)
        return rgb[cv, cu] * inside[:, None], a[cv, cu] * inside

    GU, GV = np.meshgrid((np.arange(w * f) + 0.5) / f, (np.arange(h * f) + 0.5) / f)
    out_rgb, out_a = jinc2(fetch, GU.ravel(), GV.ravel())
    return np.dstack([out_rgb.reshape(h * f, w * f, 3), (out_a * 255).reshape(h * f, w * f)]).astype(np.uint8)
