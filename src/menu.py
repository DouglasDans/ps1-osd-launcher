import glob
import logging
import os
import random
import subprocess
import sys
import time

import pygame

log = logging.getLogger("ps1.menu")

from src import controller
from src.controller import Action
from src.ps1_font import PS1Font

IS_PI = os.path.exists("/dev/fb0")

PS1_FONT_SHEET = "assets/ps1_font_sheet.png"
PS1_FONT_JSON = "assets/ps1_font.json"
BG_PATH = "assets/ps1-bios.jpg"
MAIN_MENU_IMG_PATH = "assets/main-menu.png"
SPLASH_DIR = "assets/items-splash"
CURSOR_PATH = "assets/cursor.png"

WIDTH, HEIGHT = 1920, 1080

# Uppercase glyphs are ~280px tall in the sprite sheet (4x upscaled, fd172ac).
# Scale to match the original font sizes (42px / 58px).
FONT_SCALE = 0.175
FONT_SCALE_SELECTED = 0.2175
ITEM_X = 210
ITEM_START_Y = 320
ITEM_SPACING = 160

COLOR_TEXT = (255, 255, 255)
COLOR_SHADOW = (0, 0, 0)
SHADOW_OFFSET = (5, 5)

SPLASH_BASE_W = 500
SPLASH_BASE_H = 130
VISIBLE_COUNT = 4
SLIDE_DURATION = 120

CURSOR_HEIGHT = 50
CURSOR_Y_OVERLAP_RATIO = 0.5


def _load_splashes(apps: list) -> list[pygame.Surface]:
    paths = sorted(glob.glob(f"{SPLASH_DIR}/*.png"))
    random.shuffle(paths)
    result = []
    for i in range(len(apps)):
        path = paths[i % len(paths)]
        img = pygame.image.load(path).convert_alpha()
        result.append(pygame.transform.smoothscale(img, (SPLASH_BASE_W, SPLASH_BASE_H)))
    return result


def _cursor_position(splash_x: int, splash_w: int, text_y: int, text_h: int, cursor_h: int) -> tuple[int, int]:
    cursor_x = splash_x + splash_w // 2
    cursor_y = text_y + text_h - int(cursor_h * CURSOR_Y_OVERLAP_RATIO)
    return cursor_x, cursor_y


def run(screen: pygame.Surface, apps: list[tuple[str, str]]) -> None:
    font = PS1Font(PS1_FONT_SHEET, PS1_FONT_JSON)

    bg_raw = pygame.image.load(BG_PATH).convert()
    bg = pygame.transform.scale(bg_raw, (WIDTH, HEIGHT))

    _raw_menu_img = pygame.image.load(MAIN_MENU_IMG_PATH).convert()
    orig_w, orig_h = _raw_menu_img.get_size()
    box_h = 90
    box_w = int(orig_w * box_h / orig_h)
    main_menu_img = pygame.transform.scale(_raw_menu_img, (box_w, box_h))

    splashes = _load_splashes(apps)

    cursor_raw = pygame.image.load(CURSOR_PATH).convert_alpha()
    cursor_w = int(cursor_raw.get_width() * CURSOR_HEIGHT / cursor_raw.get_height())
    cursor_img = pygame.transform.smoothscale(cursor_raw, (cursor_w, CURSOR_HEIGHT))

    selected = 0
    scroll_offset = 0
    slide_start = 0
    slide_dir = 0
    clock = pygame.time.Clock()
    transitioning = False
    transition_start = 0
    TRANSITION_DURATION = 800

    while True:
        now = pygame.time.get_ticks()

        if transitioning:
            t = min((now - transition_start) / TRANSITION_DURATION, 1.0)
            if t >= 1.0:
                screen = _launch(apps[selected][1])
                transitioning = False
                bg_raw = pygame.image.load(BG_PATH).convert()
                bg = pygame.transform.scale(bg_raw, (WIDTH, HEIGHT))
                t = 0.0
            selected_color = (int(255 * (1 - t)), int(255 * (1 - t)), int(255 * (1 - t)))
        else:
            selected_color = COLOR_TEXT
            for event in pygame.event.get():
                action = controller.get_action(event)

                if action in (Action.QUIT, Action.BACK):
                    return

                if action == Action.UP:
                    selected = (selected - 1) % len(apps)
                    if selected == len(apps) - 1:
                        new_offset = max(0, len(apps) - VISIBLE_COUNT)
                    else:
                        new_offset = min(scroll_offset, selected)
                    if new_offset != scroll_offset:
                        slide_dir = -1
                        slide_start = now
                    scroll_offset = new_offset

                if action == Action.DOWN:
                    selected = (selected + 1) % len(apps)
                    if selected == 0:
                        new_offset = 0
                    else:
                        new_offset = max(scroll_offset, selected - VISIBLE_COUNT + 1)
                    if new_offset != scroll_offset:
                        slide_dir = 1
                        slide_start = now
                    scroll_offset = new_offset

                if action == Action.CONFIRM:
                    log.info("Selecionado: %s", apps[selected][0])
                    transitioning = True
                    transition_start = now

        _draw(screen, bg, font, main_menu_img, apps, splashes, cursor_img, selected, scroll_offset, now, slide_start, slide_dir, selected_color)
        pygame.display.flip()
        clock.tick(30)


def _launch(cmd: str) -> pygame.Surface:
    quit_after = cmd.startswith("!")
    if quit_after:
        cmd = cmd[1:]

    log.info("Lançando: %s", cmd)
    pygame.display.quit()

    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("SDL_")}
    t0 = time.monotonic()
    result = subprocess.run(cmd, shell=True, env=clean_env)
    elapsed = time.monotonic() - t0
    log.info("App encerrado: returncode=%d, duração=%.1fs", result.returncode, elapsed)

    if quit_after:
        sys.exit(0)

    pygame.display.init()
    flags = pygame.FULLSCREEN if IS_PI else 0
    screen = pygame.display.set_mode((WIDTH, HEIGHT), flags)
    pygame.mouse.set_visible(False)
    pygame.event.clear()
    return screen


def _draw(
    screen: pygame.Surface,
    bg: pygame.Surface,
    font: PS1Font,
    main_menu_img: pygame.Surface,
    apps: list[tuple[str, str]],
    splashes: list[pygame.Surface],
    cursor_img: pygame.Surface,
    selected: int,
    scroll_offset: int,
    now: int,
    slide_start: int,
    slide_dir: int,
    selected_color: tuple[int, int, int] = COLOR_TEXT,
) -> None:
    screen.blit(bg, (0, 0))
    screen.blit(main_menu_img, (WIDTH - main_menu_img.get_width() - 60, 60))

    elapsed = now - slide_start
    if slide_start > 0 and elapsed < SLIDE_DURATION:
        t = elapsed / SLIDE_DURATION
        y_offset = int((1 - t) * ITEM_SPACING * slide_dir)
    else:
        y_offset = 0

    visible = apps[scroll_offset : scroll_offset + VISIBLE_COUNT]
    for i, (name, _) in enumerate(visible):
        real_index = scroll_offset + i
        is_selected = (real_index == selected)
        splash_surf = splashes[real_index]
        splash_w = splash_surf.get_width()
        splash_h = splash_surf.get_height()

        y_center = ITEM_START_Y + i * ITEM_SPACING + y_offset
        splash_y = y_center - splash_h // 2

        screen.blit(splash_surf, (ITEM_X, splash_y))

        scale = FONT_SCALE_SELECTED if is_selected else FONT_SCALE
        text = name.upper()
        text_w = font.text_width(text, scale=scale)
        text_h = max((int(font.glyphs[c].get_size()[1] * scale) for c in text if c in font.glyphs), default=int(font.line_height * scale))
        splash_cx = ITEM_X + splash_w // 2
        text_x = splash_cx - text_w // 2
        text_y = splash_y + (splash_h - text_h) // 2

        color = selected_color if is_selected else COLOR_TEXT
        font.render(screen, text, text_x + SHADOW_OFFSET[0], text_y + SHADOW_OFFSET[1], color=COLOR_SHADOW, scale=scale)
        font.render(screen, text, text_x, text_y, color=color, scale=scale)

        if is_selected:
            cursor_x, cursor_y = _cursor_position(ITEM_X, splash_w, text_y, text_h, cursor_img.get_height())
            screen.blit(cursor_img, (cursor_x, cursor_y))
