#!/usr/bin/env python3
"""Script para popular o banco de dados com a lista de municípios.

Fontes:
- API IBGE: todos os 5.570 municípios brasileiros
- municipios_seed.json: 10 municípios para testes rápidos

Uso:
    python scripts/seed_municipios.py              # Todos os municípios via IBGE
    python scripts/seed_municipios.py --seed-only  # Apenas seed de 10 municípios
    python scripts/seed_municipios.py --uf SP      # Apenas municípios de SP
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Adicionar raiz do projeto ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from tqdm import tqdm

from config.settings import settings
from core.database import Database
from core.logger import get_logger, setup_logging
from integrations.ibge import IBGEClient

logger = get_logger("seed_municipios")


async def _seed_from_ibge(db: Database, uf: str | None = None) -> int:
    """Busca municípios da API IBGE e salva no banco."""
    client = IBGEClient()
    if uf:
        municipios = await client.get_municipios_by_uf(uf)
    else:
        municipios = await client.get_all_municipios()

    logger.info(f"Inserindo {len(municipios)} município(s) no banco...")

    for municipio in tqdm(municipios, desc="Inserindo municípios"):
        await db.upsert_municipio(
            {
                "codigo_ibge": municipio["codigo_ibge"],
                "nome": municipio["nome"],
                "uf": municipio["uf"],
                "populacao": municipio.get("populacao"),
                "status_descoberta": "pendente",
                "ultima_atualizacao": None,
            }
        )

    return len(municipios)


async def _seed_from_json(db: Database) -> int:
    """Carrega municípios do arquivo municipios_seed.json."""
    seed_path = Path(__file__).parent.parent / "config" / "municipios_seed.json"
    with open(seed_path, encoding="utf-8") as f:
        municipios = json.load(f)

    logger.info(f"Inserindo {len(municipios)} município(s) do seed...")
    for m in municipios:
        await db.upsert_municipio(
            {
                "codigo_ibge": m["codigo_ibge"],
                "nome": m["nome"],
                "uf": m["uf"],
                "populacao": m.get("populacao"),
                "status_descoberta": "pendente",
                "ultima_atualizacao": None,
            }
        )
    return len(municipios)


@click.command()
@click.option(
    "--uf",
    default=None,
    help="Filtrar por UF (ex: SP, MG). Se não informado, carrega todos.",
)
@click.option(
    "--seed-only",
    is_flag=True,
    default=False,
    help="Carregar apenas os 10 municípios do seed JSON em vez da API IBGE.",
)
@click.option(
    "--db-path",
    default=None,
    help="Caminho do banco SQLite (padrão: configurado no .env).",
)
def main(uf: str | None, seed_only: bool, db_path: str | None) -> None:
    """Popula o banco de dados com a lista de municípios brasileiros."""
    setup_logging(settings.LOG_LEVEL, settings.LOG_FILE)

    async def _run() -> None:
        path = db_path or settings.db_path
        db = Database(path)
        await db.connect()
        try:
            if seed_only:
                count = await _seed_from_json(db)
                click.echo(f"✓ {count} municípios do seed inseridos em '{path}'")
            else:
                count = await _seed_from_ibge(db, uf)
                click.echo(f"✓ {count} municípios inseridos em '{path}'")
        finally:
            await db.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
