import logging
import os

import pygame
from pygame._sdl2 import controller as sdl2_ctrl
from enum import Enum, auto

log = logging.getLogger("ps1.controller")

GAMECONTROLLERDB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "gamecontrollerdb.txt"
)


class Action(Enum):
    UP = auto()
    DOWN = auto()
    CONFIRM = auto()
    BACK = auto()
    QUIT = auto()


_DEBOUNCE_MS = 150
_last_ms: dict = {}


def init() -> None:
    # Carrega banco de mapeamentos comunitário (mesmo usado pelo RetroArch)
    if os.path.exists(GAMECONTROLLERDB):
        os.environ["SDL_GAMECONTROLLERCONFIG_FILE"] = GAMECONTROLLERDB

    sdl2_ctrl.init()

    count = sdl2_ctrl.get_count()
    for i in range(count):
        if sdl2_ctrl.is_controller(i):
            sdl2_ctrl.Controller(i)
    log.info("Controles detectados na inicialização: %d", count)


def get_action(event: pygame.event.Event) -> Action | None:
    if event.type == pygame.QUIT:
        return Action.QUIT

    # Novo controle conectado em runtime
    if event.type == pygame.CONTROLLERDEVICEADDED:
        if sdl2_ctrl.is_controller(event.device_index):
            sdl2_ctrl.Controller(event.device_index)
            log.info("Controle conectado: índice=%d", event.device_index)
        return None

    action = None

    # Teclado (fallback dev)
    if event.type == pygame.KEYDOWN:
        if event.key == pygame.K_UP:
            action = Action.UP
        elif event.key == pygame.K_DOWN:
            action = Action.DOWN
        elif event.key == pygame.K_RETURN:
            action = Action.CONFIRM
        elif event.key == pygame.K_ESCAPE:
            action = Action.BACK

    # GameController API — PS, Xbox, 8BitDo e qualquer controle no SDL_GameControllerDB
    # Valores inteiros SDL2 estáveis (constantes nomeadas indisponíveis em algumas versões)
    # Ref: https://wiki.libsdl.org/SDL2/SDL_GameControllerButton
    elif event.type == pygame.CONTROLLERBUTTONDOWN:
        if event.button == 11:   # SDL_CONTROLLER_BUTTON_DPAD_UP
            action = Action.UP
        elif event.button == 12:   # SDL_CONTROLLER_BUTTON_DPAD_DOWN
            action = Action.DOWN
        elif event.button == 0:    # SDL_CONTROLLER_BUTTON_A
            action = Action.CONFIRM
        elif event.button == 1:    # SDL_CONTROLLER_BUTTON_B
            action = Action.BACK

    if action is None:
        return None

    now = pygame.time.get_ticks()
    if now - _last_ms.get(action, 0) < _DEBOUNCE_MS:
        return None
    _last_ms[action] = now
    return action
