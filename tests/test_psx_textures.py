import os

import numpy as np
import pygame

from src.psx import textures
from src.psx.trace import Field, Trace
from tests.test_psx_hires import _Recorder, _sprite_scene


def _scene_trace():
    rec = _Recorder()
    _sprite_scene(rec)  # one 4x4 checker sprite (white / transparent)
    return Trace([Field(1000, rec.words)], [])


def _read_rgba(path):
    surf = pygame.image.load(path)
    w, h = surf.get_size()
    return np.frombuffer(pygame.image.tobytes(surf, "RGBA"), dtype=np.uint8).reshape(h, w, 4).copy()


def test_texture_key_depends_on_content_and_size():
    a = np.zeros((2, 2, 4), dtype=np.uint8)
    b = a.copy()
    b[0, 0, 0] = 1

    assert textures.texture_key(a) == textures.texture_key(a.copy())
    assert textures.texture_key(a) != textures.texture_key(b)
    assert textures.texture_key(a) != textures.texture_key(np.zeros((1, 4, 4), dtype=np.uint8))


def test_dump_writes_each_drawn_texture_once_with_transparency(tmp_path):
    paths = textures.dump(_scene_trace(), str(tmp_path))

    assert len(paths) == 1  # the quad's two triangles read the same texels
    img = _read_rgba(paths[0])
    assert img.shape == (5, 5, 4)  # UVs 0..4: the rect includes the max vertex UV
    assert img[0, 1].tolist() == [255, 255, 255, 255]  # index 1: white, opaque
    assert img[0, 0, 3] == 0  # index 0: transparent
    assert os.path.basename(paths[0]) == textures.texture_key(img) + ".png"


def test_split_and_merge_round_trip_through_an_upscaler(tmp_path):
    native = textures.dump(_scene_trace(), str(tmp_path / "native"))[0]
    rgb_path, alpha_path = textures.split(native, str(tmp_path / "split"))

    def upscale(path):  # stand-in for Real-ESRGAN x4: nearest
        img = _read_rgba(path)[..., :3]
        big = np.ascontiguousarray(np.kron(img, np.ones((4, 4, 1), dtype=np.uint8)))
        out = str(tmp_path / ("up_" + os.path.basename(path)))
        pygame.image.save(pygame.image.frombuffer(big.tobytes(), big.shape[1::-1], "RGB"), out)
        return out

    merged = textures.merge(upscale(rgb_path), upscale(alpha_path), str(tmp_path / "hd" / os.path.basename(native)))

    img = _read_rgba(merged)
    assert img.shape == (20, 20, 4)
    assert img[0:4, 4:8].reshape(-1, 4).tolist() == [[255, 255, 255, 255]] * 16
    assert img[0:4, 0:4, 3].max() == 0


def test_split_fills_transparent_texels_with_the_lightest_color(tmp_path):
    native = textures.dump(_scene_trace(), str(tmp_path / "native"))[0]

    rgb_path, _ = textures.split(native, str(tmp_path / "split"))

    rgb = _read_rgba(rgb_path)
    assert rgb[0, 0, :3].tolist() == [255, 255, 255]  # no dark halo for the upscaler


def test_load_reads_replacements_keyed_by_file_name(tmp_path):
    textures.dump(_scene_trace(), str(tmp_path))

    found = textures.load(str(tmp_path))

    assert len(found) == 1
    key, img = next(iter(found.items()))
    assert img.shape == (5, 5, 4) and img.dtype == np.float32
    assert img[0, 1, 3] == 1.0


def test_load_of_a_missing_directory_is_empty(tmp_path):
    assert textures.load(str(tmp_path / "none")) == {}


def _ink_on_matte():
    """Navy ink antialiased against white, like the BIOS "TM" texture."""
    ink, matte = np.array([0, 40, 80]), np.array([240, 240, 240])
    t = np.array([0.0, 0.25, 0.5, 1.0])  # ink coverage per column
    rgb = np.rint(matte + (ink - matte) * t[:, None]).astype(np.uint8)
    img = np.zeros((2, 5, 4), dtype=np.uint8)
    img[:, :4, :3] = rgb
    img[:, :4, 3] = 255
    return img  # last column: transparent texel


def test_unmatte_turns_the_antialiasing_into_transparency():
    out = textures.unmatte(_ink_on_matte())

    assert (out[..., :3][out[..., 3] > 0] == [0, 40, 80]).all()  # pure ink color
    assert out[0, :, 3].tolist() == [0, 64, 128, 255, 0]  # coverage as alpha


def test_unmatte_leaves_multicolor_textures_alone():
    img = _ink_on_matte()
    img[0, 1, :3] = [255, 0, 0]  # not on the ink-matte line

    assert np.array_equal(textures.unmatte(img), img)


def test_split_feeds_the_upscaler_ink_and_coverage(tmp_path):
    path = os.path.join(tmp_path, "t.png")
    textures._save(_ink_on_matte(), path)

    rgb_path, alpha_path = textures.split(path, str(tmp_path / "split"))

    assert (_read_rgba(rgb_path)[..., :3] == [0, 40, 80]).all()
    assert _read_rgba(alpha_path)[0, :, 0].tolist() == [0, 64, 128, 255, 0]
