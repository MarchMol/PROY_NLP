"""Configuración de logging de la aplicación (no confundir con la auditoría JSONL)."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configura el logger raíz una sola vez, con salida a stderr."""
    root = logging.getLogger()
    if getattr(root, "_marioqa_configured", False):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(level)
    # Librerías ruidosas
    for noisy in ("httpx", "httpcore", "chromadb", "sentence_transformers", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._marioqa_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    """Atajo para obtener un logger con nombre de módulo."""
    return logging.getLogger(name)
