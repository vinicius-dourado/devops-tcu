"""Orquestrador principal do pipeline Radar Transparência."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from agents.auditor import AuditorAgent
from agents.cartographer import CartographerAgent
from agents.miner import MinerAgent
from agents.scout import ScoutAgent
from config.settings import settings
from core.database import Database
from core.logger import get_logger
from core.models import FonteDados, Municipio, StatusDescoberta, StatusMapeamento
from core.state import PipelineState

logger = get_logger("Orchestrator")
console = Console()


class PipelineOrchestrator:
    """Coordena a execução dos 4 agentes em sequência para cada município.

    Pipeline por município:
        Scout → Cartógrafo → Minerador → Auditor

    Suporta:
    - Execução em lotes (batch_size)
    - Retomada automática (resume=True)
    - Modo dry-run (sem persistência)
    - Filtro de etapas (only_scout)
    - Rate limiting entre municípios
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.state = PipelineState(db)
        self.scout = ScoutAgent(db)
        self.cartographer = CartographerAgent(db)
        self.miner = MinerAgent(db)
        self.auditor = AuditorAgent(db)

    async def run(
        self,
        municipios_ibge: list[str] | None = None,
        batch_size: int | None = None,
        dry_run: bool = False,
        only_scout: bool = False,
        only_cartographer: bool = False,
        only_miner: bool = False,
        resume: bool = True,
        uf: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Executa o pipeline completo.

        Args:
            municipios_ibge: Lista de códigos IBGE. Se None, usa todos do banco.
            batch_size: Municípios processados em paralelo por lote.
            dry_run: Se True, não persiste dados.
            only_scout: Se True, executa apenas o Scout.
            only_cartographer: Se True, executa Scout + Cartógrafo.
            only_miner: Se True, executa Scout + Cartógrafo + Minerador.
            resume: Se True, pula etapas já concluídas.
            uf: Filtrar municípios por UF.
            limit: Limitar número de municípios processados.

        Returns:
            Relatório de execução com estatísticas.
        """
        batch_size = batch_size or settings.BATCH_SIZE
        iniciado_em = datetime.now()

        # Obter lista de municípios
        if municipios_ibge:
            municipio_rows = []
            for ibge in municipios_ibge:
                row = await self.db.get_municipio(ibge)
                if row:
                    municipio_rows.append(row)
        else:
            municipio_rows = await self.db.list_municipios(uf=uf, limit=limit)

        if not municipio_rows:
            logger.warning("Nenhum município encontrado para processar.")
            return {"status": "vazio", "total": 0}

        total = len(municipio_rows)
        logger.info(
            f"Iniciando pipeline: {total} município(s) | "
            f"batch={batch_size} | dry_run={dry_run} | resume={resume}"
        )

        stats: dict[str, int] = {
            "total": total,
            "scout_ok": 0,
            "cartographer_ok": 0,
            "miner_ok": 0,
            "auditor_ok": 0,
            "erros": 0,
        }

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Processando municípios...", total=total)

            # Processar em lotes
            for i in range(0, total, batch_size):
                batch = municipio_rows[i: i + batch_size]
                batch_tasks = [
                    self._process_municipio(
                        row, dry_run, only_scout, only_cartographer, only_miner, resume, stats
                    )
                    for row in batch
                ]
                await asyncio.gather(*batch_tasks, return_exceptions=True)
                progress.advance(task, len(batch))

                # Rate limiting entre lotes
                if i + batch_size < total:
                    await asyncio.sleep(settings.RATE_LIMIT_DELAY_SECONDS)

        finalizado_em = datetime.now()
        duracao = (finalizado_em - iniciado_em).total_seconds()

        relatorio = {
            **stats,
            "dry_run": dry_run,
            "iniciado_em": iniciado_em.isoformat(),
            "finalizado_em": finalizado_em.isoformat(),
            "duracao_segundos": duracao,
        }

        console.print(f"\n[bold green]Pipeline concluído em {duracao:.1f}s[/bold green]")
        console.print(f"  Scout: {stats['scout_ok']}/{total}")
        console.print(f"  Cartógrafo: {stats['cartographer_ok']}/{total}")
        console.print(f"  Minerador: {stats['miner_ok']}/{total}")
        console.print(f"  Auditor: {stats['auditor_ok']}/{total}")
        console.print(f"  Erros: {stats['erros']}")

        return relatorio

    async def _process_municipio(
        self,
        municipio_row: dict[str, Any],
        dry_run: bool,
        only_scout: bool,
        only_cartographer: bool,
        only_miner: bool,
        resume: bool,
        stats: dict[str, int],
    ) -> None:
        """Processa um único município pelo pipeline completo."""
        municipio = Municipio(**municipio_row)
        ibge = municipio.codigo_ibge

        try:
            # ── Etapa 1: Scout ─────────────────────────────────────────────
            if not await self.state.should_skip(ibge, "scout", resume):
                log_id = await self.state.mark_started(ibge, "scout")
                try:
                    fontes = await self.scout.execute(municipio, dry_run=dry_run)
                    await self.state.mark_done(ibge, "scout", log_id, {"fontes": len(fontes)})
                    stats["scout_ok"] += 1
                    logger.info(f"[{ibge}] Scout: {len(fontes)} fontes")
                except Exception as e:
                    await self.state.mark_error(ibge, "scout", log_id, str(e))
                    stats["erros"] += 1
                    logger.error(f"[{ibge}] Scout falhou: {e}")
                    return
            else:
                stats["scout_ok"] += 1

            if only_scout:
                return

            # ── Etapa 2: Cartógrafo ────────────────────────────────────────
            fontes_rows = await self.db.get_fontes_municipio(ibge)
            fontes = [self._row_to_fonte(row) for row in fontes_rows]

            if not await self.state.should_skip(ibge, "cartographer", resume):
                log_id = await self.state.mark_started(ibge, "cartographer")
                mapas_ok = 0
                try:
                    for fonte in fontes:
                        if fonte.status_mapeamento != StatusMapeamento.PENDENTE:
                            continue
                        mapa = await self.cartographer.execute(fonte, dry_run=dry_run)
                        if mapa:
                            mapas_ok += 1
                        await asyncio.sleep(settings.RATE_LIMIT_DELAY_SECONDS)
                    await self.state.mark_done(ibge, "cartographer", log_id, {"mapas": mapas_ok})
                    stats["cartographer_ok"] += 1
                except Exception as e:
                    await self.state.mark_error(ibge, "cartographer", log_id, str(e))
                    stats["erros"] += 1
                    logger.error(f"[{ibge}] Cartógrafo falhou: {e}")
                    return
            else:
                stats["cartographer_ok"] += 1

            if only_cartographer:
                return

            # ── Etapa 3: Minerador ────────────────────────────────────────
            fontes_rows = await self.db.get_fontes_municipio(ibge)
            fontes = [self._row_to_fonte(row) for row in fontes_rows]

            if not await self.state.should_skip(ibge, "miner", resume):
                log_id = await self.state.mark_started(ibge, "miner")
                total_lics = total_cts = total_pubs = 0
                try:
                    for fonte in fontes:
                        if not fonte.mapa_navegacao:
                            continue
                        lics, cts, pubs = await self.miner.execute(fonte, dry_run=dry_run)
                        total_lics += len(lics)
                        total_cts += len(cts)
                        total_pubs += len(pubs)
                        await asyncio.sleep(settings.RATE_LIMIT_DELAY_SECONDS)
                    await self.state.mark_done(
                        ibge, "miner", log_id,
                        {"licitacoes": total_lics, "contratos": total_cts, "publicacoes": total_pubs}
                    )
                    stats["miner_ok"] += 1
                except Exception as e:
                    await self.state.mark_error(ibge, "miner", log_id, str(e))
                    stats["erros"] += 1
                    logger.error(f"[{ibge}] Minerador falhou: {e}")
                    return
            else:
                stats["miner_ok"] += 1

            if only_miner:
                return

            # ── Etapa 4: Auditor ──────────────────────────────────────────
            if not await self.state.should_skip(ibge, "auditor", resume):
                log_id = await self.state.mark_started(ibge, "auditor")
                try:
                    relatorio = await self.auditor.execute(ibge, dry_run=dry_run)
                    await self.state.mark_done(
                        ibge, "auditor", log_id,
                        {"anomalias": len(relatorio.get("anomalias", []))}
                    )
                    stats["auditor_ok"] += 1
                except Exception as e:
                    await self.state.mark_error(ibge, "auditor", log_id, str(e))
                    stats["erros"] += 1
                    logger.error(f"[{ibge}] Auditor falhou: {e}")
            else:
                stats["auditor_ok"] += 1

        except Exception as e:
            stats["erros"] += 1
            logger.error(f"[{ibge}] Erro inesperado no pipeline: {e}")

    def _row_to_fonte(self, row: dict[str, Any]) -> FonteDados:
        """Converte linha do banco em objeto FonteDados."""
        from core.models import StatusMapeamento, TipoFonte, TipoSistema
        return FonteDados(
            id=row.get("id"),
            municipio_ibge=row["municipio_ibge"],
            tipo=TipoFonte(row["tipo"]),
            url=row["url"],
            tipo_sistema=TipoSistema(row.get("tipo_sistema", "desconhecido")),
            status_mapeamento=StatusMapeamento(row.get("status_mapeamento", "pendente")),
            mapa_navegacao=row.get("mapa_navegacao"),
            ultima_coleta=row.get("ultima_coleta"),
            notas=row.get("notas"),
        )
