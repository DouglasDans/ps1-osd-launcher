import logging
import os
import sys

import pygame

from src import bios_intro, controller, logger
from src.config import load_apps
from src.intro import hide_tty, play_intro, restore_tty, show_splash, wait_system_ready
from src.menu import run as run_menu

log = logging.getLogger("ps1.launcher")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APPS_INI = os.path.join(BASE_DIR, "apps.ini")
INTRO_VIDEO = os.path.join(BASE_DIR, "assets", "intro.mp4")
BIOS_TRACE = os.path.join(BASE_DIR, "assets", "bios_intro.trace")
BIOS_AUDIO = os.path.join(BASE_DIR, "assets", "bios_intro.wav")
SPLASH_IMAGE = os.path.join(BASE_DIR, "assets", "last_frame.png")

WIDTH, HEIGHT = 1920, 1080

IS_PI = os.path.exists("/dev/fb0")


GAMECONTROLLERDB = os.path.join(BASE_DIR, "assets", "gamecontrollerdb.txt")


def setup_environment() -> None:
    if os.path.exists(GAMECONTROLLERDB):
        os.environ["SDL_GAMECONTROLLERCONFIG_FILE"] = GAMECONTROLLERDB

    if IS_PI:
        os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
        os.environ["SDL_FBDEV"] = "/dev/fb0"
        os.environ["SDL_AUDIODRIVER"] = "alsa"


def main() -> None:
    logger.setup()
    log.info("Launcher iniciado")

    setup_environment()

    try:
        apps = load_apps(APPS_INI)
    except (FileNotFoundError, KeyError) as e:
        log.error("Erro ao carregar apps.ini: %s", e)
        sys.exit(1)

    if IS_PI:
        hide_tty()

    # Inicializa só o necessário — sem mixer para não ocupar o ALSA
    # (o menu não tem som; a intro da BIOS abre o mixer e o deixa tocando o fim
    # do reverb, liberado pelo menu antes de lançar um app; o fallback em vídeo
    # toca o áudio pelo SDL do ffpyplayer)
    pygame.display.init()
    pygame.font.init()
    flags = pygame.FULLSCREEN if IS_PI else 0
    screen = pygame.display.set_mode((WIDTH, HEIGHT), flags)
    pygame.display.set_caption("PS1 Launcher")
    pygame.mouse.set_visible(False)

    if bios_intro.play(screen, BIOS_TRACE, BIOS_AUDIO):
        # the BIOS cuts from the Sony screen to its (purple) menu background
        show_splash(screen, SPLASH_IMAGE)
    else:
        play_intro(screen, INTRO_VIDEO, SPLASH_IMAGE)
    wait_system_ready()

    controller.init()
    run_menu(screen, apps)

    pygame.display.quit()
    pygame.font.quit()
    restore_tty()
    log.info("Launcher encerrado")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.getLogger("ps1.launcher").exception("Exceção não tratada")
        sys.exit(1)
