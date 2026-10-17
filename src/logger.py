import logging

# Sem timestamp: roda sob systemd e o journal já carimba cada linha.
_FMT = "%(levelname)-8s | %(name)s | %(message)s"


def setup() -> None:
    root = logging.getLogger("ps1")
    root.setLevel(logging.DEBUG)

    if root.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FMT))
    root.addHandler(handler)
