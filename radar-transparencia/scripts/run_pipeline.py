#!/usr/bin/env python3
"""Script principal para executar o pipeline do Radar Transparência.

Uso:
    python scripts/run_pipeline.py --uf SP --limit 10       # 10 municípios de SP
    python scripts/run_pipeline.py --municipio 3550308      # São Paulo específico
    python scripts/run_pipeline.py --all --batch-size 20    # Todos os municípios
    python scripts/run_pipeline.py --resume                 # Retomar de onde parou
    python scripts/run_pipeline.py --only-scout --uf MG     # Só fase de descoberta, MG
    python scripts/run_pipeline.py --uf SP --limit 3 --dry-run  # Teste sem salvar
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Adicionar raiz do projeto ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from rich.console import Console
from rich.panel import Panel

from config.settings import settings
from core.database import Database
from core.logger import get_logger, setup_logging
from pipeline.orchestrator import PipelineOrchestrator

logger = get_logger("run_pipeline")
console = Console()


@click.command()
@click.option("--uf", default=None, help="Filtrar municípios por UF (ex: SP, MG).")
@click.option(
    "--limit",
    default=None,
    type=int,
    help="Limitar número de municípios a processar.",
)
@click.option(
    "--municipio",
    "municipio_ibge",
    default=None,
    help="Código IBGE de um município específico.",
)
@click.option(
    "--all",
    "process_all",
    is_flag=True,
    default=False,
    help="Processar todos os municípios do banco.",
)
@click.option(
    "--batch-size",
    default=None,
    type=int,
    help=f"Municípios processados em paralelo (padrão: {settings.BATCH_SIZE}).",
)
@click.option(
    "--resume/--no-resume",
    default=True,
    help="Retomar de onde parou (padrão: --resume).",
)
@click.option(
    "--only-scout",
    is_flag=True,
    default=False,
    help="Executar apenas a etapa de descoberta (Scout).",
)
@click.option(
    "--only-cartographer",
    is_flag=True,
    default=False,
    help="Executar Scout + Cartógrafo.",
)
@click.option(
    "--only-miner",
    is_flag=True,
    default=False,
    help="Executar Scout + Cartógrafo + Minerador (sem Auditor).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Executar sem salvar dados no banco.",
)
@click.option(
    "--db-path",
    default=None,
    help="Caminho do banco SQLite.",
)
@click.option(
    "--output-report",
    default=None,
    help="Salvar relatório de execução em JSON neste caminho.",
)
def cli(
    uf: str | None,
    limit: int | None,
    municipio_ibge: str | None,
    process_all: bool,
    batch_size: int | None,
    resume: bool,
    only_scout: bool,
    only_cartographer: bool,
    only_miner: bool,
    dry_run: bool,
    db_path: str | None,
    output_report: str | None,
) -> None:
    """Executa o pipeline Radar Transparência."""
    setup_logging(settings.LOG_LEVEL, settings.LOG_FILE)

    # Validações básicas
    if not any([uf, municipio_ibge, process_all, limit]):
        console.print(
            "[red]Especifique --uf, --municipio, --limit ou --all para selecionar municípios.[/red]\n"
            "Exemplo: python scripts/run_pipeline.py --uf SP --limit 3 --dry-run"
        )
        raise SystemExit(1)

    if not settings.ANTHROPIC_API_KEY:
        console.print(
            "[red]ANTHROPIC_API_KEY não configurada.[/red]\n"
            "Configure no arquivo .env ou como variável de ambiente."
        )
        raise SystemExit(1)

    # Montar parâmetros
    etapa = "pipeline completo"
    if only_scout:
        etapa = "Scout (descoberta)"
    elif only_cartographer:
        etapa = "Scout + Cartógrafo"
    elif only_miner:
        etapa = "Scout + Cartógrafo + Minerador"

    console.print(
        Panel(
            f"[bold cyan]Radar Transparência[/bold cyan]\n"
            f"Etapa: {etapa}\n"
            f"UF: {uf or 'todas'} | Limite: {limit or 'sem limite'}\n"
            f"Batch: {batch_size or settings.BATCH_SIZE} | Resume: {resume} | Dry-run: {dry_run}",
            title="Pipeline",
        )
    )

    async def _run() -> None:
        path = db_path or settings.db_path
        db = Database(path)
        await db.connect()

        try:
            orchestrator = PipelineOrchestrator(db)

            municipios_ibge = [municipio_ibge] if municipio_ibge else None

            relatorio = await orchestrator.run(
                municipios_ibge=municipios_ibge,
                batch_size=batch_size,
                dry_run=dry_run,
                only_scout=only_scout,
                only_cartographer=only_cartographer,
                only_miner=only_miner,
                resume=resume,
                uf=uf,
                limit=limit,
            )

            if output_report:
                with open(output_report, "w", encoding="utf-8") as f:
                    json.dump(relatorio, f, ensure_ascii=False, indent=2)
                console.print(f"[green]Relatório salvo em: {output_report}[/green]")

        finally:
            await db.close()

    asyncio.run(_run())


if __name__ == "__main__":
    cli()
