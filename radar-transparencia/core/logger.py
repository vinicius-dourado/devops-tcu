"""Logger estruturado com suporte a rich para o Radar Transparência."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_loggers: dict[str, logging.Logger] = {}
_console = Console(stderr=True)


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configura o sistema de logging globalmente.

    Args:
        level: Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Caminho para arquivo de log (opcional).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [
        RichHandler(
            console=_console,
            rich_tracebacks=True,
            markup=True,
            show_time=True,
            show_path=False,
        )
    ]

    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=numeric_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Retorna um logger com o nome dado.

    Args:
        name: Nome do logger (geralmente o nome da classe ou módulo).

    Returns:
        Logger configurado com rich handler.
    """
    if name not in _loggers:
        logger = logging.getLogger(f"radar_transparencia.{name}")
        _loggers[name] = logger
    return _loggers[name]
