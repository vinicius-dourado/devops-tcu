"""Configurações centrais do Radar Transparência."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Carrega .env da raiz do projeto ou do diretório pai
_ROOT = Path(__file__).parent.parent
for _env_path in [_ROOT / ".env", _ROOT.parent / ".env"]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break


class Settings:
    """Configurações carregadas de variáveis de ambiente."""

    # API Keys
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Banco de dados
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite:///./radar_transparencia.db"
    )

    @property
    def db_path(self) -> str:
        """Extrai o caminho do arquivo SQLite da DATABASE_URL."""
        url = self.DATABASE_URL
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///"):]
            # Se o caminho é relativo, resolve em relação à raiz do projeto
            if not Path(path).is_absolute():
                return str(_ROOT / path)
            return path
        return "radar_transparencia.db"

    # Configurações de execução
    BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "10"))
    MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "5"))
    RATE_LIMIT_DELAY_SECONDS: float = float(
        os.getenv("RATE_LIMIT_DELAY_SECONDS", "2")
    )
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")

    # Querido Diário API
    QUERIDO_DIARIO_API_URL: str = os.getenv(
        "QUERIDO_DIARIO_API_URL", "https://queridodiario.ok.org.br/api"
    )

    # Logs
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: str | None = os.getenv("LOG_FILE") or None

    # Playwright
    USE_PLAYWRIGHT: bool = os.getenv("USE_PLAYWRIGHT", "false").lower() == "true"


settings = Settings()
