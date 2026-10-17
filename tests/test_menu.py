from src import menu
from src.menu import _cursor_position


def test_cursor_position_sits_below_text_baseline():
    x, y = _cursor_position(splash_x=210, splash_w=500, text_y=340, text_h=60, cursor_h=50)

    assert y > 340 + 60 - 50
    assert y < 340 + 60 + 50


def test_cursor_position_sits_at_splash_center_x():
    x, y = _cursor_position(splash_x=210, splash_w=500, text_y=340, text_h=60, cursor_h=50)

    assert x == 210 + 500 // 2


def test_cursor_position_ignores_text_height_horizontally():
    x_short, _ = _cursor_position(splash_x=210, splash_w=500, text_y=340, text_h=40, cursor_h=50)
    x_tall, _ = _cursor_position(splash_x=210, splash_w=500, text_y=340, text_h=80, cursor_h=50)

    assert x_short == x_tall


def test_cursor_position_follows_splash_width():
    x_narrow, _ = _cursor_position(splash_x=210, splash_w=200, text_y=340, text_h=60, cursor_h=50)
    x_wide, _ = _cursor_position(splash_x=210, splash_w=800, text_y=340, text_h=60, cursor_h=50)

    assert x_wide > x_narrow


def test_journal_tag_is_lowercase_slug_of_app_name():
    assert menu._journal_tag("Duckstation") == "duckstation"
    assert menu._journal_tag("PPSSPP") == "ppsspp"
    assert menu._journal_tag("Mega Drive (Genesis)") == "mega-drive-genesis"


def test_launch_argv_wraps_command_in_systemd_cat_with_app_tag():
    argv = menu._launch_argv("PPSSPP", "cage ~/emulators/ppsspp/PPSSPPSDL")
    assert argv == ["systemd-cat", "-t", "ppsspp", "--", "sh", "-c", "cage ~/emulators/ppsspp/PPSSPPSDL"]
