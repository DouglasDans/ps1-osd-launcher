"""HD texture replacements, in the spirit of DuckStation's texture packs.

Every texture a trace draws is identified by a hash of its decoded texels
(RGBA, transparent texels with alpha 0). An HD version saved as <key>.png
is drawn instead of it by src.psx.hires.

    python -m src.psx.textures <in.trace> <out_dir> <realesrgan-ncnn-vulkan> [model]

dumps the textures, upscales them with Real-ESRGAN (color and transparency
separately: the upscaler only handles RGB) and writes the HD versions.
"""

import hashlib
import os
import subprocess
import sys
import tempfile

import numpy as np
import pygame

from src.psx.gpu import GPU, rgb8

DEFAULT_MODEL = "realesrgan-x4plus-anime"  # flat colors and sharp lettering


def texture_rect(gpu: GPU, uv, clut) -> np.ndarray:
    """Texels a primitive with these vertex UVs reads, as RGBA uint8 (h, w, 4)."""
    u0, u1 = min(t[0] for t in uv), max(t[0] for t in uv)
    v0, v1 = min(t[1] for t in uv), max(t[1] for t in uv)
    U, V = np.meshgrid(np.arange(u0, u1 + 1), np.arange(v0, v1 + 1))
    texel = gpu._fetch_texels(U & 0xFF, V & 0xFF, clut).astype(np.int64)
    alpha = np.where(texel != 0, 255, 0).astype(np.uint8)
    return np.dstack([rgb8(texel), alpha])


def texture_key(rgba: np.ndarray) -> str:
    h = hashlib.sha1(np.asarray(rgba.shape, dtype=np.int64).tobytes())
    h.update(np.ascontiguousarray(rgba).tobytes())
    return h.hexdigest()[:16]


class _Collector(GPU):
    def __init__(self) -> None:
        super().__init__()
        self.found: dict[str, np.ndarray] = {}

    def _triangle(self, v, c, uv, *, textured, clut, **style) -> None:
        if textured:
            rect = texture_rect(self, uv, clut)
            self.found.setdefault(texture_key(rect), rect)
        super()._triangle(v, c, uv, textured=textured, clut=clut, **style)


def collect(trace) -> dict[str, np.ndarray]:
    """Every distinct texture the trace draws, by key."""
    gpu = _Collector()
    for i, f in enumerate(trace.fields):
        gpu.field = i & 1
        for port, word in f.gpu:
            if port:
                gpu.gp1(word)
            else:
                gpu.gp0(word)
    return gpu.found


def _save(img: np.ndarray, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    mode = "RGBA" if img.shape[2] == 4 else "RGB"
    surf = pygame.image.frombuffer(np.ascontiguousarray(img).tobytes(), (img.shape[1], img.shape[0]), mode)
    pygame.image.save(surf, path)
    return path


def _read(path: str) -> np.ndarray:
    surf = pygame.image.load(path)
    w, h = surf.get_size()
    return np.frombuffer(pygame.image.tobytes(surf, "RGBA"), dtype=np.uint8).reshape(h, w, 4).copy()


def dump(trace, out_dir: str) -> list[str]:
    """Writes every texture the trace draws as <key>.png (native size)."""
    return [_save(img, os.path.join(out_dir, key + ".png")) for key, img in collect(trace).items()]


def split(path: str, out_dir: str) -> tuple[str, str]:
    """RGBA texture -> an RGB image and an alpha (grayscale) image for an
    RGB-only upscaler. Transparent texels take the lightest opaque color
    (what the edges were antialiased against), so no dark halo appears."""
    img = _read(path)
    opaque = img[..., 3] > 0
    rgb = img[..., :3].copy()
    if opaque.any():
        colors = rgb[opaque].astype(np.int64)
        rgb[~opaque] = colors[np.argmax(colors.sum(axis=1))]
    name = os.path.splitext(os.path.basename(path))[0]
    alpha = np.repeat(img[..., 3:4], 3, axis=2)
    return (_save(rgb, os.path.join(out_dir, name + "_rgb.png")),
            _save(alpha, os.path.join(out_dir, name + "_alpha.png")))


def merge(rgb_path: str, alpha_path: str, out_path: str) -> str:
    """Joins upscaled color and alpha images back into an RGBA texture."""
    rgb, alpha = _read(rgb_path), _read(alpha_path)
    return _save(np.dstack([rgb[..., :3], alpha[..., 0]]), out_path)


def load(directory: str) -> dict[str, np.ndarray]:
    """Replacement textures by key: float32 (h, w, 4), RGB 0-255, alpha 0-1."""
    if not os.path.isdir(directory):
        return {}
    found = {}
    for name in sorted(os.listdir(directory)):
        key, ext = os.path.splitext(name)
        if ext.lower() != ".png":
            continue
        img = _read(os.path.join(directory, name)).astype(np.float32)
        img[..., 3] /= 255.0
        found[key] = img
    return found


def make_hd(trace, out_dir: str, upscaler: str, model: str = DEFAULT_MODEL) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        native, parts, upscaled = (os.path.join(tmp, d) for d in ("native", "parts", "up"))
        for path in dump(trace, native):
            split(path, parts)
        os.makedirs(upscaled)
        subprocess.run([upscaler, "-i", parts, "-o", upscaled, "-n", model, "-f", "png"],
                       check=True, cwd=os.path.dirname(os.path.abspath(upscaler)))
        out = []
        for name in sorted(os.listdir(upscaled)):
            if name.endswith("_rgb.png"):
                key = name[: -len("_rgb.png")]
                out.append(merge(os.path.join(upscaled, name), os.path.join(upscaled, key + "_alpha.png"),
                                 os.path.join(out_dir, key + ".png")))
        return out


if __name__ == "__main__":
    from src.psx.trace import load as load_trace

    if len(sys.argv) not in (4, 5):
        sys.exit("usage: python -m src.psx.textures <in.trace> <out_dir> <realesrgan-ncnn-vulkan> [model]")
    for written in make_hd(load_trace(sys.argv[1]), sys.argv[2], sys.argv[3], *sys.argv[4:]):
        print(written)
