import gc
import weakref

import pygame
import pytest

from src import controller


class FakeController:
    # Como no pygame-ce: o controle só fica aberto enquanto o objeto existe
    opened: list = []

    def __init__(self, index):
        FakeController.opened.append(weakref.ref(self))


@pytest.fixture
def fake_sdl2(monkeypatch):
    FakeController.opened = []
    monkeypatch.setattr(controller, "_controllers", [])
    monkeypatch.setattr(controller.sdl2_ctrl, "init", lambda: None)
    monkeypatch.setattr(controller.sdl2_ctrl, "get_count", lambda: 1)
    monkeypatch.setattr(controller.sdl2_ctrl, "is_controller", lambda i: True)
    monkeypatch.setattr(controller.sdl2_ctrl, "Controller", FakeController)


def _alive() -> int:
    gc.collect()
    return sum(ref() is not None for ref in FakeController.opened)


def test_init_keeps_controllers_open(fake_sdl2):
    controller.init()
    assert _alive() == 1


def test_hotplugged_controller_stays_open(fake_sdl2):
    event = pygame.event.Event(pygame.CONTROLLERDEVICEADDED, device_index=0)
    assert controller.get_action(event) is None
    assert _alive() == 1
