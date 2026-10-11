import fcntl
import logging
import os
import subprocess
import time

import pygame

log = logging.getLogger("ps1.intro")


def play_intro(screen: pygame.Surface, video_path: str, splash_path: str | None = None) -> None:
    if not os.path.exists(video_path):
        log.warning("Vídeo de intro não encontrado: %s", video_path)
        _show_splash(screen, splash_path)
        return

    try:
        from ffpyplayer.player import MediaPlayer
    except ImportError:
        log.warning("ffpyplayer indisponível — pulando intro")
        _show_splash(screen, splash_path)
        return

    log.info("Reproduzindo intro: %s", video_path)
    player = MediaPlayer(video_path, ff_opts={"out_fmt": "rgb24"})
    try:
        while True:
            pygame.event.get()

            frame, val = player.get_frame()
            if val == "eof":
                break
            if frame is None:
                time.sleep(0.010)
                continue

            img, _ts = frame
            if val > 0:
                time.sleep(val)
            _blit_frame(screen, img)
            pygame.display.flip()
    except Exception:
        log.exception("Erro durante reprodução da intro")
        _show_splash(screen, splash_path)
    finally:
        player.close_player()

    log.info("Intro encerrada")


def _blit_frame(screen: pygame.Surface, img) -> None:
    w, h = img.get_size()
    data = img.to_bytearray()[0]
    pitch = img.get_linesizes()[0]

    # frombuffer exige linhas contíguas; remove padding de alinhamento se houver
    if pitch != w * 3:
        data = b"".join(bytes(data[i * pitch : i * pitch + w * 3]) for i in range(h))

    surf = pygame.image.frombuffer(data, (w, h), "RGB")
    if (w, h) != screen.get_size():
        surf = pygame.transform.scale(surf, screen.get_size())
    screen.blit(surf, (0, 0))


def wait_system_ready(timeout: float = 30.0) -> None:
    """Segura no frame atual até o boot do sistema concluir (só no Pi).

    O launcher sobe cedo no boot; aqui esperamos o systemd sair de
    "starting" antes de liberar o menu. Qualquer outro estado (running,
    degraded, erro) libera — o menu nunca pode ficar refém de um unit
    quebrado ou de rede lenta.
    """
    if not os.path.exists("/dev/fb0"):
        return

    t0 = time.monotonic()
    state = "unknown"
    while time.monotonic() - t0 < timeout:
        try:
            result = subprocess.run(
                ["systemctl", "is-system-running"],
                capture_output=True, text=True, timeout=5,
            )
            state = result.stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            state = "unknown"

        if state != "starting":
            break

        pygame.event.get()
        time.sleep(0.5)

    log.info(
        "Sistema pronto: estado=%s, aguardado=%.1fs",
        state, time.monotonic() - t0,
    )


def _show_splash(screen: pygame.Surface, splash_path: str | None) -> None:
    if not splash_path or not os.path.exists(splash_path):
        return
    img = pygame.image.load(splash_path).convert()
    img = pygame.transform.scale(img, screen.get_size())
    screen.blit(img, (0, 0))
    pygame.display.flip()


_KDSETMODE = 0x4B3A
_KD_GRAPHICS = 0x01
_KD_TEXT = 0x00


def hide_tty() -> None:
    try:
        with open("/dev/tty1", "w") as tty:
            tty.write("\033[?25l\033[2J\033[H")
        with open("/dev/tty1", "wb") as tty:
            fcntl.ioctl(tty, _KDSETMODE, _KD_GRAPHICS)
    except OSError:
        pass


def restore_tty() -> None:
    try:
        with open("/dev/tty1", "wb") as tty:
            fcntl.ioctl(tty, _KDSETMODE, _KD_TEXT)
    except OSError:
        pass
