import numpy as np

from src.psx.gpu import GPU, rgb15


def _draw_area(gpu, x1=0, y1=0, x2=1023, y2=511):
    gpu.gp0(0xE3000000 | (y1 << 10) | x1)
    gpu.gp0(0xE4000000 | (y2 << 10) | x2)
    gpu.gp0(0xE5000000)


def _vertex(x, y):
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _upload(gpu, x, y, w, h, pixels):
    gpu.gp0(0xA0000000)
    gpu.gp0(_vertex(x, y))
    gpu.gp0((h << 16) | w)
    flat = list(pixels) + ([0] if len(pixels) % 2 else [])
    for i in range(0, len(flat), 2):
        gpu.gp0(flat[i] | (flat[i + 1] << 16))


def test_rgb15_packs_8bit_channels_dropping_low_bits():
    assert rgb15(0xFF, 0x00, 0x00) == 0x001F
    assert rgb15(0x00, 0xFF, 0x00) == 0x03E0
    assert rgb15(0x00, 0x00, 0xFF) == 0x7C00
    assert rgb15(0x08, 0x10, 0x18) == (1 | 2 << 5 | 3 << 10)


def test_cpu_to_vram_upload_writes_halfwords_row_by_row():
    gpu = GPU()
    _upload(gpu, 10, 20, 3, 2, [1, 2, 3, 4, 5, 6])

    assert gpu.vram[20, 10:13].tolist() == [1, 2, 3]
    assert gpu.vram[21, 10:13].tolist() == [4, 5, 6]


def test_vram_to_cpu_command_is_consumed_without_breaking_the_stream():
    gpu = GPU()
    gpu.gp0(0xC0000000)
    gpu.gp0(_vertex(0, 0))
    gpu.gp0(0x00010010)
    _draw_area(gpu)
    gpu.gp0(0x28FFFFFF)
    for x, y in [(0, 0), (4, 0), (0, 4), (4, 4)]:
        gpu.gp0(_vertex(x, y))

    assert gpu.vram[0, 0] == 0x7FFF


def test_flat_quad_excludes_right_and_bottom_edges():
    gpu = GPU()
    _draw_area(gpu)
    gpu.gp0(0x280000FF)  # flat opaque quad, red
    for x, y in [(2, 3), (6, 3), (2, 5), (6, 5)]:
        gpu.gp0(_vertex(x, y))

    drawn = np.argwhere(gpu.vram != 0)
    assert drawn[:, 0].min() == 3 and drawn[:, 0].max() == 4
    assert drawn[:, 1].min() == 2 and drawn[:, 1].max() == 5
    assert gpu.vram[3, 2] == 0x001F


def test_drawing_is_clipped_to_drawing_area_and_offset_applied():
    gpu = GPU()
    _draw_area(gpu, 0, 0, 3, 3)
    gpu.gp0(0xE5000000 | (1 << 11) | 1)  # offset (1, 1)
    gpu.gp0(0x28FFFFFF)
    for x, y in [(0, 0), (10, 0), (0, 10), (10, 10)]:
        gpu.gp0(_vertex(x, y))

    drawn = np.argwhere(gpu.vram != 0)
    assert drawn.min(axis=0).tolist() == [1, 1]
    assert drawn.max(axis=0).tolist() == [3, 3]


def test_gouraud_triangle_interpolates_between_vertex_colors():
    gpu = GPU()
    _draw_area(gpu)
    gpu.gp0(0x30000000)  # gouraud tri, vertex 0 black
    gpu.gp0(_vertex(0, 0))
    gpu.gp0(0x000000F8)  # vertex 1 red
    gpu.gp0(_vertex(64, 0))
    gpu.gp0(0x00000000)
    gpu.gp0(_vertex(0, 64))

    reds = [int(gpu.vram[0, x]) & 0x1F for x in (0, 16, 32, 48)]
    assert reds == sorted(reds)
    assert reds[0] == 0 and reds[-1] > 20


def test_dithering_applies_to_gouraud_polygons_only_when_enabled():
    flat_color = 0x00000086  # red 0x86 sits between two 5-bit steps
    results = []
    for dither in (False, True):
        gpu = GPU()
        _draw_area(gpu)
        gpu.gp0(0xE1000000 | (dither << 9))
        gpu.gp0(0x30000000 | flat_color)
        gpu.gp0(_vertex(0, 0))
        gpu.gp0(flat_color)
        gpu.gp0(_vertex(16, 0))
        gpu.gp0(flat_color)
        gpu.gp0(_vertex(0, 16))
        results.append({int(v) & 0x1F for v in gpu.vram[0:4, 0:4].ravel()})

    assert results[0] == {0x86 >> 3}
    assert results[1] == {(0x86 - 4) >> 3, (0x86 + 3) >> 3}


def test_textured_quad_reads_4bit_texels_through_clut():
    gpu = GPU()
    _draw_area(gpu)
    _upload(gpu, 0, 500, 16, 1, [0, 0x1111, 0x2222] + [0] * 13)  # CLUT at (0, 500)
    _upload(gpu, 640, 0, 1, 1, [0x2121])  # texels: 1, 2, 1, 2
    clut = (500 << 6) | 0
    tpage = 10  # x=640, y=0, 4bpp
    gpu.gp0(0x2D808080)  # raw textured quad
    gpu.gp0(_vertex(0, 0))
    gpu.gp0((clut << 16) | 0x0000)
    gpu.gp0(_vertex(4, 0))
    gpu.gp0((tpage << 16) | 0x0004)
    gpu.gp0(_vertex(0, 1))
    gpu.gp0(0x0100)
    gpu.gp0(_vertex(4, 1))
    gpu.gp0(0x0104)

    assert gpu.vram[0, 0:4].tolist() == [0x1111, 0x2222, 0x1111, 0x2222]


def test_texel_zero_is_transparent():
    gpu = GPU()
    _draw_area(gpu)
    gpu.vram[0, 0:4] = 0x0155
    _upload(gpu, 0, 500, 16, 1, [0, 0x1111] + [0] * 14)
    _upload(gpu, 640, 0, 1, 1, [0x0101])  # texels: 1, 0, 1, 0
    gpu.gp0(0x2D808080)
    gpu.gp0(_vertex(0, 0))
    gpu.gp0((500 << 6) << 16)
    gpu.gp0(_vertex(4, 0))
    gpu.gp0((10 << 16) | 4)
    gpu.gp0(_vertex(0, 1))
    gpu.gp0(0x0100)
    gpu.gp0(_vertex(4, 1))
    gpu.gp0(0x0104)

    assert gpu.vram[0, 0:4].tolist() == [0x1111, 0x0155, 0x1111, 0x0155]


def test_semi_transparent_average_blends_with_background():
    gpu = GPU()
    _draw_area(gpu)
    gpu.vram[:, :] = rgb15(0, 0, 0xF8)
    gpu.gp0(0xE1000000)  # mode 0: B/2 + F/2
    gpu.gp0(0x2A0000F8)  # flat semi-transparent quad, red
    for x, y in [(0, 0), (2, 0), (0, 2), (2, 2)]:
        gpu.gp0(_vertex(x, y))

    px = int(gpu.vram[0, 0])
    assert (px & 0x1F, px >> 10 & 0x1F) == (15, 15)


def test_interlaced_field_skips_lines_of_the_displayed_field():
    gpu = GPU()
    gpu.gp1(0x08000027)  # 640x480 interlaced
    _draw_area(gpu)
    gpu.gp0(0xE1000000)  # drawing to display area prohibited
    gpu.field = 0
    gpu.gp0(0x28FFFFFF)
    for x, y in [(0, 0), (2, 0), (0, 4), (2, 4)]:
        gpu.gp0(_vertex(x, y))

    rows = [int(gpu.vram[y, 0] != 0) for y in range(4)]
    assert rows == [0, 1, 0, 1]


def test_display_returns_rgb_of_the_display_area():
    gpu = GPU()
    gpu.gp1(0x03000000)  # display enable
    gpu.gp1(0x08000027)
    gpu.gp1(0x05000000 | (2 << 10))  # display start (0, 2)
    gpu.gp1(0x06C60260)
    gpu.gp1(0x0703FC10)
    gpu.vram[2, 0] = rgb15(0xF8, 0, 0)

    img = gpu.display_rgb()
    assert img.shape == (478, 640, 3)
    assert img[0, 0].tolist() == [0xFF, 0, 0]


def test_display_is_black_until_enabled():
    gpu = GPU()
    gpu.gp1(0x08000027)
    gpu.gp1(0x06C60260)
    gpu.gp1(0x0703FC10)
    gpu.vram[:, :] = 0x7FFF

    assert not gpu.display_rgb().any()
