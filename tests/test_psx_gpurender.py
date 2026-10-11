import numpy as np
import pygame
import pytest

from src.psx.gpurender import RendererGPU, open_renderer
from tests.test_psx_hires import (_checker_texture, _display_640x480i, _flat_quad, _Recorder,
                                  _sprite_scene, _textured_quad)


@pytest.fixture
def renderer(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    pygame.display.init()
    pygame.display.set_mode((640, 480))
    r = open_renderer(pygame.display.get_surface())
    assert r is not None
    yield r
    pygame.display.quit()


def _gpu(renderer, scale=(1, 1), **kw):
    size = (round(640 * scale[0]), round(480 * scale[1]))
    gpu = RendererGPU(renderer, size, **kw)
    _display_640x480i(gpu)
    return gpu


def test_flat_quad_covers_the_scaled_area(renderer):
    gpu = _gpu(renderer, scale=(2, 2))
    _flat_quad(gpu, 0x0000FF, 10, 20, 14, 23)

    img = gpu.read_display()
    ys, xs = np.nonzero(img[..., 0])
    assert (xs.min(), xs.max()) == (20, 27)
    assert (ys.min(), ys.max()) == (40, 45)


def test_colors_are_true_color(renderer):
    gpu = _gpu(renderer)
    _flat_quad(gpu, 0x0000B4, 0, 0, 8, 8)

    assert gpu.read_display()[2, 2, 0] == 0xB4


def test_gouraud_is_smooth(renderer):
    gpu = _gpu(renderer, scale=(2, 2))
    gpu.gp0(0x38000000)
    gpu.gp0(0)
    gpu.gp0(0x00FFFFFF)
    gpu.gp0(256)
    gpu.gp0(0x00000000)
    gpu.gp0(16 << 16)
    gpu.gp0(0x00FFFFFF)
    gpu.gp0((16 << 16) | 256)

    row = gpu.read_display()[10, 4:508, 0].astype(int)
    assert np.all(np.diff(row) >= 0)
    assert len(np.unique(row)) > 200


def test_interlaced_fields_are_drawn_progressively(renderer):
    gpu = _gpu(renderer)
    gpu.field = 1
    _flat_quad(gpu, 0x00FF00, 0, 0, 8, 8)

    assert gpu.read_display()[0:8, 0:8, 1].min() == 0xFF


def test_native_vram_is_still_updated(renderer):
    gpu = _gpu(renderer, scale=(2, 2))
    _flat_quad(gpu, 0x0000FF, 0, 0, 4, 4)

    assert gpu.vram[1, 1] == 0x001F


def test_textures_are_drawn_from_the_native_vram(renderer):
    gpu = _gpu(renderer, scale=(2, 2), texture_filter="nearest")
    _checker_texture(gpu)
    _textured_quad(gpu, 100, 100, 104, 104, 0, 0, 4, 4)

    block = gpu.read_display()[201:207:2, 201:207:2, 0]  # texel centers
    assert block.tolist() == [[0, 255, 0], [255, 0, 255], [0, 255, 0]]


def test_texture_modulation_is_neutral_at_0x80(renderer):
    gpu = _gpu(renderer, texture_filter="nearest")
    _checker_texture(gpu)
    _textured_quad(gpu, 0, 0, 4, 4, 0, 0, 4, 4, color=0x404040)

    assert abs(int(gpu.read_display()[0, 1, 0]) - 0x7F) <= 1


def test_replacement_textures_are_drawn_instead_of_the_native_ones(renderer):
    from src.psx import textures
    from src.psx.trace import Field, Trace

    rec = _Recorder()
    _sprite_scene(rec)
    (key, _), = textures.collect(Trace([Field(1000, rec.words)], [])).items()
    hd = np.zeros((16, 16, 4), dtype=np.float32)
    hd[..., 0] = 200.0
    hd[:, :8, 3] = 1.0
    gpu = RendererGPU(renderer, (1280, 960), replacements={key: hd})

    for port, word in rec.words:
        (gpu.gp1 if port else gpu.gp0)(word)

    sprite = gpu.read_display()[200:208, 200:208]
    assert sprite[:, :3, 0].min() >= 190 and sprite[:, :3, 1].max() == 0
    assert sprite[:, 6:, 0].max() == 0


def test_lazy_fields_draw_nothing(renderer):
    gpu = _gpu(renderer)
    gpu.lazy = True
    _flat_quad(gpu, 0x0000FF, 0, 0, 8, 8)
    gpu.lazy = False

    assert not gpu.read_display().any()
    assert gpu.vram[1, 1] == 0x001F


def test_each_new_frame_is_a_new_token_and_can_be_shown(renderer):
    gpu = _gpu(renderer)
    a, b = gpu.frame(), gpu.frame()

    assert a is not b
    gpu.show(b, pygame.display.get_surface())


def test_display_flags_keep_fullscreen_as_the_pygame_constant():
    from src.psx.gpurender import display_flags

    class Fullscreen:
        def get_flags(self):
            return 0x80000000 | 0x10  # unsigned, as SDL reports it

    class Windowed:
        def get_flags(self):
            return 0x10

    assert display_flags(Fullscreen()) == pygame.FULLSCREEN
    assert display_flags(Windowed()) == 0
