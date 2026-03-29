"""Gerenciamento de estado do pipeline para suporte a retomada."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.database import Database
from core.logger import get_logger

logger = get_logger("PipelineState")

ETAPAS = ["scout", "cartographer", "miner", "auditor"]


class PipelineState:
    """Rastreia o progresso do pipeline por município para permitir retomada."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def get_status(self, municipio_ibge: str) -> dict[str, str]:
        """Retorna o status de cada etapa para um município.

        Returns:
            Dict mapeando etapa → status ('pendente', 'em_andamento', 'concluido', 'erro').
        """
        status: dict[str, str] = {e: "pendente" for e in ETAPAS}
        for etapa in ETAPAS:
            log = await self.db.get_ultimo_log(municipio_ibge, etapa)
            if log:
                status[etapa] = log["status"]
        return status

    async def mark_started(self, municipio_ibge: str, etapa: str) -> str:
        """Marca o início de uma etapa. Retorna o id do log."""
        log_id = await self.db.log_execucao(
            {
                "municipio_ibge": municipio_ibge,
                "etapa": etapa,
                "status": "em_andamento",
                "iniciado_em": datetime.now().isoformat(),
            }
        )
        logger.debug(f"[{municipio_ibge}] Etapa '{etapa}' iniciada (log_id={log_id})")
        return log_id

    async def mark_done(
        self,
        municipio_ibge: str,
        etapa: str,
        log_id: str,
        detalhes: dict[str, Any] | None = None,
    ) -> None:
        """Marca a conclusão bem-sucedida de uma etapa."""
        await self.db.log_execucao(
            {
                "id": log_id,
                "municipio_ibge": municipio_ibge,
                "etapa": etapa,
                "status": "concluido",
                "iniciado_em": datetime.now().isoformat(),
                "finalizado_em": datetime.now().isoformat(),
                "detalhes": detalhes or {},
            }
        )
        logger.debug(f"[{municipio_ibge}] Etapa '{etapa}' concluída")

    async def mark_error(
        self,
        municipio_ibge: str,
        etapa: str,
        log_id: str,
        mensagem: str,
    ) -> None:
        """Marca falha em uma etapa."""
        await self.db.log_execucao(
            {
                "id": log_id,
                "municipio_ibge": municipio_ibge,
                "etapa": etapa,
                "status": "erro",
                "mensagem": mensagem,
                "iniciado_em": datetime.now().isoformat(),
                "finalizado_em": datetime.now().isoformat(),
            }
        )
        logger.warning(f"[{municipio_ibge}] Etapa '{etapa}' com erro: {mensagem}")

    async def should_skip(
        self, municipio_ibge: str, etapa: str, resume: bool = True
    ) -> bool:
        """Verifica se uma etapa deve ser pulada (já foi concluída com sucesso).

        Args:
            municipio_ibge: Código IBGE do município.
            etapa: Nome da etapa.
            resume: Se True, pula etapas já concluídas. Se False, sempre reexecuta.

        Returns:
            True se a etapa deve ser pulada.
        """
        if not resume:
            return False
        log = await self.db.get_ultimo_log(municipio_ibge, etapa)
        return bool(log and log["status"] == "concluido")

    async def get_pending_municipios(
        self, etapa: str, municipios_ibge: list[str]
    ) -> list[str]:
        """Retorna os municípios que ainda não concluíram a etapa dada."""
        pending = []
        for ibge in municipios_ibge:
            log = await self.db.get_ultimo_log(ibge, etapa)
            if not log or log["status"] != "concluido":
                pending.append(ibge)
        return pending
