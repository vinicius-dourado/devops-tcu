"""Agendamento de execuções periódicas do pipeline."""

from __future__ import annotations

import asyncio
from typing import Any

from core.database import Database
from core.logger import get_logger
from pipeline.orchestrator import PipelineOrchestrator

logger = get_logger("Scheduler")


class PipelineScheduler:
    """Agenda execuções periódicas do pipeline usando APScheduler (opcional).

    Se APScheduler não estiver instalado, provê apenas execução manual
    com intervalos configuráveis.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.orchestrator = PipelineOrchestrator(db)
        self._scheduler: Any = None

    def start(
        self,
        cron_expression: str = "0 2 * * *",
        uf: str | None = None,
        batch_size: int = 10,
    ) -> None:
        """Inicia o agendador com expressão cron.

        Args:
            cron_expression: Expressão cron (padrão: todo dia às 2h).
            uf: Filtrar por UF (ou None para todos os municípios).
            batch_size: Municípios por lote.
        """
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import]
            from apscheduler.triggers.cron import CronTrigger  # type: ignore[import]

            self._scheduler = AsyncIOScheduler()
            parts = cron_expression.split()
            if len(parts) == 5:
                minute, hour, day, month, day_of_week = parts
            else:
                minute, hour, day, month, day_of_week = "0", "2", "*", "*", "*"

            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
            )
            self._scheduler.add_job(
                self._run_job,
                trigger=trigger,
                kwargs={"uf": uf, "batch_size": batch_size},
                id="radar_pipeline",
                replace_existing=True,
            )
            self._scheduler.start()
            logger.info(f"Agendador iniciado com cron: '{cron_expression}'")

        except ImportError:
            logger.warning(
                "APScheduler não instalado. Instale com: pip install apscheduler\n"
                "Usando loop manual como fallback."
            )

    def stop(self) -> None:
        """Para o agendador."""
        if self._scheduler:
            self._scheduler.shutdown()
            logger.info("Agendador parado.")

    async def _run_job(self, **kwargs: Any) -> None:
        """Job executado pelo agendador."""
        logger.info("Iniciando execução agendada do pipeline...")
        try:
            await self.orchestrator.run(**kwargs)
        except Exception as e:
            logger.error(f"Erro na execução agendada: {e}")

    async def run_every(
        self,
        interval_seconds: int,
        **pipeline_kwargs: Any,
    ) -> None:
        """Executa o pipeline repetidamente com intervalo fixo (fallback sem APScheduler).

        Args:
            interval_seconds: Intervalo em segundos entre execuções.
            **pipeline_kwargs: Passados para PipelineOrchestrator.run().
        """
        logger.info(f"Loop de execução a cada {interval_seconds}s iniciado.")
        while True:
            try:
                await self.orchestrator.run(**pipeline_kwargs)
            except Exception as e:
                logger.error(f"Erro na execução do pipeline: {e}")
            logger.info(f"Próxima execução em {interval_seconds}s...")
            await asyncio.sleep(interval_seconds)
