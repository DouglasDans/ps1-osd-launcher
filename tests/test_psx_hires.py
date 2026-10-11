import numpy as np
import pytest

from src.psx.hires import HiResGPU


def _vertex(x, y):
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _display_640x480i(gpu):
    for word in (0x03000000, 0x08000027, 0x06C60260, 0x0703FC10):
        gpu.gp1(word)
    gpu.gp0(0xE3000000)
    gpu.gp0(0xE4000000 | (511 << 10) | 1023)
    gpu.gp0(0xE5000000)


def _upload(gpu, x, y, w, h, pixels):
    gpu.gp0(0xA0000000)
    gpu.gp0(_vertex(x, y))
    gpu.gp0((h << 16) | w)
    flat = list(pixels) + ([0] if len(pixels) % 2 else [])
    for i in range(0, len(flat), 2):
        gpu.gp0(flat[i] | (flat[i + 1] << 16))


def _flat_quad(gpu, color, x0, y0, x1, y1):
    gpu.gp0(0x28000000 | color)
    for x, y in [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]:
        gpu.gp0(_vertex(x, y))


def _textured_quad(gpu, x0, y0, x1, y1, u0, v0, u1, v1, clut=(0, 480), page=(640, 0), color=0x808080):
    clut_word = (clut[1] << 6) | (clut[0] >> 4)
    page_word = (page[0] // 64) | ((page[1] // 256) << 4)  # 4-bit CLUT
    gpu.gp0(0x2C000000 | color)
    gpu.gp0(_vertex(x0, y0))
    gpu.gp0((clut_word << 16) | (v0 << 8) | u0)
    gpu.gp0(_vertex(x1, y0))
    gpu.gp0((page_word << 16) | (v0 << 8) | u1)
    gpu.gp0(_vertex(x0, y1))
    gpu.gp0((v1 << 8) | u0)
    gpu.gp0(_vertex(x1, y1))
    gpu.gp0((v1 << 8) | u1)


def _checker_texture(gpu, white=0x7FFF):
    """4-bit 4x4 texture at (640, 0): index 1 (white) / 0 (transparent) checker,
    CLUT at (0, 480)."""
    _upload(gpu, 0, 480, 2, 1, [0, white])
    rows = []
    for v in range(4):
        idx = [(u + v) & 1 for u in range(4)]
        rows.append(idx[0] | idx[1] << 4 | idx[2] << 8 | idx[3] << 12)
    _upload(gpu, 640, 0, 1, 4, rows)


def test_display_image_has_the_output_size():
    gpu = HiResGPU(scale=(3, 2.25))
    _display_640x480i(gpu)

    assert gpu.display_rgb().shape == (round(478 * 2.25), 640 * 3, 3)


def test_flat_quad_covers_the_scaled_area_exactly():
    gpu = HiResGPU(scale=(2, 2))
    _display_640x480i(gpu)
    _flat_quad(gpu, 0x0000FF, 10, 20, 14, 23)

    img = gpu.display_rgb()
    ys, xs = np.nonzero(img[..., 0])
    # display starts at VRAM line 0 here; quad covers x 10..13, y 20..22
    assert (xs.min(), xs.max()) == (20, 27)
    assert (ys.min(), ys.max()) == (40, 45)


def test_colors_are_true_color_not_15_bit():
    gpu = HiResGPU(scale=(1, 1))
    _display_640x480i(gpu)
    _flat_quad(gpu, 0x0000B4, 0, 0, 8, 8)

    assert gpu.display_rgb()[2, 2, 0] == 0xB4  # 15-bit would give 0xB5


def test_gouraud_is_smooth_and_undithered():
    gpu = HiResGPU(scale=(2, 2))
    _display_640x480i(gpu)
    gpu.gp0(0xE1000200)  # dither on: the enhanced renderer ignores it
    gpu.gp0(0x38000000)  # gouraud quad, black on the left, white on the right
    gpu.gp0(_vertex(0, 0))
    gpu.gp0(0x00FFFFFF)
    gpu.gp0(_vertex(256, 0))
    gpu.gp0(0x00000000)
    gpu.gp0(_vertex(0, 16))
    gpu.gp0(0x00FFFFFF)
    gpu.gp0(_vertex(256, 16))

    row = gpu.display_rgb()[10, :512, 0].astype(int)
    assert np.all(np.diff(row) >= 0)
    assert len(np.unique(row)) > 200
    assert np.array_equal(gpu.display_rgb()[10, :512], gpu.display_rgb()[11, :512])


def test_interlaced_fields_are_drawn_progressively():
    gpu = HiResGPU(scale=(1, 1))
    _display_640x480i(gpu)
    gpu.field = 1
    _flat_quad(gpu, 0x00FF00, 0, 0, 8, 8)

    img = gpu.display_rgb()
    assert img[0:8, 0:8, 1].min() == 0xFF


def test_native_vram_is_still_updated_for_textures():
    gpu = HiResGPU(scale=(2, 2))
    _display_640x480i(gpu)
    _flat_quad(gpu, 0x0000FF, 0, 0, 4, 4)

    assert gpu.vram[1, 1] == 0x001F


def test_nearest_textures_map_each_texel_to_a_scaled_block():
    gpu = HiResGPU(scale=(2, 2), texture_filter="nearest")
    _display_640x480i(gpu)
    _checker_texture(gpu)
    _textured_quad(gpu, 100, 100, 104, 104, 0, 0, 4, 4)

    block = gpu.display_rgb()[200:208, 200:208, 0]
    expected = np.kron([[(u + v) & 1 for u in range(4)] for v in range(4)], np.ones((2, 2))) * 0xFF
    assert np.array_equal(block, expected)


def test_texture_modulation_is_neutral_at_0x80():
    gpu = HiResGPU(scale=(1, 1), texture_filter="nearest")
    _display_640x480i(gpu)
    _checker_texture(gpu, white=0x7FFF)
    _textured_quad(gpu, 0, 0, 4, 4, 0, 0, 4, 4, color=0x404040)

    assert gpu.display_rgb()[0, 1, 0] == 0x7F  # half of white


def test_jinc2_smooths_texel_edges_with_limited_ringing():
    gpu = HiResGPU(scale=(8, 8), texture_filter="jinc2")
    _display_640x480i(gpu)
    _upload(gpu, 0, 480, 3, 1, [0, 0x7FFF, 0x2108])  # white, gray (66)
    # 8x8 texture, columns 0-3 gray, 4-7 white; only u 2..6 is drawn
    _upload(gpu, 640, 0, 2, 8, [0x2222, 0x1111] * 8)
    _textured_quad(gpu, 0, 0, 4, 4, 2, 2, 6, 6)

    row = gpu.display_rgb()[16, 0:32, 0].astype(int)
    assert row[0] == 66 and row[-1] == 255  # flat areas keep their color
    assert len(np.unique(row)) > 4  # a soft ramp, not a hard 2-level edge
    # the kernel rings; anti-ringing (0.8) keeps the dip within ~5% of the step
    assert row.min() >= 66 - 0.05 * (255 - 66)


def test_jinc2_keeps_a_flat_texture_flat():
    gpu = HiResGPU(scale=(4, 4), texture_filter="jinc2")
    _display_640x480i(gpu)
    _upload(gpu, 0, 480, 2, 1, [0, 0x3DEF])
    _upload(gpu, 640, 0, 2, 8, [0x1111] * 16)  # 8x8, only the middle is drawn
    _textured_quad(gpu, 0, 0, 4, 4, 2, 2, 6, 6)

    inner = gpu.display_rgb()[2:14, 2:14]
    assert inner.min() == inner.max()


def test_unknown_filter_is_rejected():
    with pytest.raises(ValueError):
        HiResGPU(scale=(2, 2), texture_filter="xbr")


class _Recorder:
    """Collects GPU port writes so the same drawing can be replayed as a trace."""

    def __init__(self):
        self.words = []

    def gp0(self, word):
        self.words.append((0, word))

    def gp1(self, word):
        self.words.append((1, word))


def _sprite_scene(gpu):
    _display_640x480i(gpu)
    _checker_texture(gpu)
    _textured_quad(gpu, 100, 100, 104, 104, 0, 0, 4, 4)


def test_prepare_filters_the_textures_before_they_are_drawn(monkeypatch):
    from src.psx import hires
    from src.psx.trace import Field, Trace

    rec = _Recorder()
    _sprite_scene(rec)
    trace = Trace([Field(1000, rec.words)], [])
    gpu = HiResGPU(scale=(2, 2), texture_filter="jinc2")

    gpu.prepare(trace)
    calls = []
    real = hires.jinc2
    monkeypatch.setattr(hires, "jinc2", lambda *a: calls.append(1) or real(*a))
    for port, word in rec.words:
        (gpu.gp1 if port else gpu.gp0)(word)

    assert calls == []
    assert gpu.display_rgb()[201, 203, 0] > 0  # the sprite was still drawn


def test_prepare_does_not_draw_anything():
    from src.psx.trace import Field, Trace

    rec = _Recorder()
    _sprite_scene(rec)
    gpu = HiResGPU(scale=(2, 2), texture_filter="jinc2")
    _display_640x480i(gpu)

    gpu.prepare(Trace([Field(1000, rec.words)], []))

    assert not gpu.display_rgb().any()
    assert not gpu.vram.any()
