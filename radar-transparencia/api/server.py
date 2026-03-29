"""API FastAPI para consultar dados coletados pelo Radar Transparência."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from core.database import Database
from core.logger import setup_logging

setup_logging(settings.LOG_LEVEL, settings.LOG_FILE)

_db: Database | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _db
    _db = Database(settings.db_path)
    await _db.connect()
    yield
    if _db:
        await _db.close()


app = FastAPI(
    title="Radar Transparência API",
    description="API para consultar dados de transparência pública municipal coletados pelo Radar Transparência.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _get_db() -> Database:
    if _db is None:
        raise HTTPException(status_code=503, detail="Banco de dados não disponível")
    return _db


# ── Municípios ───────────────────────────────────────────────────────────────


@app.get("/municipios", summary="Listar municípios")
async def list_municipios(
    uf: str | None = Query(None, description="Filtrar por UF (ex: SP)"),
    status: str | None = Query(None, description="Filtrar por status de descoberta"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Lista municípios com filtros opcionais."""
    db = _get_db()
    municipios = await db.list_municipios(uf=uf, status=status, limit=limit, offset=offset)
    return {"total": len(municipios), "offset": offset, "municipios": municipios}


@app.get("/municipios/{codigo_ibge}", summary="Detalhes de um município")
async def get_municipio(codigo_ibge: str) -> dict[str, Any]:
    """Retorna detalhes de um município pelo código IBGE."""
    db = _get_db()
    municipio = await db.get_municipio(codigo_ibge)
    if not municipio:
        raise HTTPException(status_code=404, detail=f"Município {codigo_ibge} não encontrado")
    fontes = await db.get_fontes_municipio(codigo_ibge)
    return {**municipio, "fontes": fontes}


# ── Licitações ───────────────────────────────────────────────────────────────


@app.get("/municipios/{codigo_ibge}/licitacoes", summary="Licitações de um município")
async def list_licitacoes(
    codigo_ibge: str,
    validado: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Lista licitações de um município."""
    db = _get_db()
    licitacoes = await db.list_licitacoes(
        municipio_ibge=codigo_ibge, validado=validado, limit=limit, offset=offset
    )
    return {"total": len(licitacoes), "offset": offset, "licitacoes": licitacoes}


@app.get("/licitacoes", summary="Todas as licitações")
async def list_all_licitacoes(
    uf: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Lista todas as licitações com filtros opcionais."""
    db = _get_db()
    if uf:
        municipios = await db.list_municipios(uf=uf)
        ibge_list = [m["codigo_ibge"] for m in municipios]
        all_lics = []
        for ibge in ibge_list[:50]:  # Limitar para não sobrecarregar
            lics = await db.list_licitacoes(municipio_ibge=ibge, limit=limit)
            all_lics.extend(lics)
        return {"total": len(all_lics), "licitacoes": all_lics}
    licitacoes = await db.list_licitacoes(limit=limit, offset=offset)
    return {"total": len(licitacoes), "offset": offset, "licitacoes": licitacoes}


# ── Contratos ────────────────────────────────────────────────────────────────


@app.get("/municipios/{codigo_ibge}/contratos", summary="Contratos de um município")
async def list_contratos(
    codigo_ibge: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Lista contratos de um município."""
    db = _get_db()
    contratos = await db.list_contratos(
        municipio_ibge=codigo_ibge, limit=limit, offset=offset
    )
    return {"total": len(contratos), "offset": offset, "contratos": contratos}


# ── Anomalias ────────────────────────────────────────────────────────────────


@app.get("/anomalias", summary="Anomalias detectadas")
async def list_anomalias(
    municipio_ibge: str | None = Query(None),
    severidade: str | None = Query(None, description="baixa | media | alta | critica"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    """Lista anomalias ordenadas por severidade."""
    db = _get_db()
    anomalias = await db.list_anomalias(
        municipio_ibge=municipio_ibge, severidade=severidade, limit=limit
    )
    return {"total": len(anomalias), "anomalias": anomalias}


# ── Estatísticas ─────────────────────────────────────────────────────────────


@app.get("/stats", summary="Estatísticas gerais")
async def get_stats() -> dict[str, Any]:
    """Retorna estatísticas gerais do sistema."""
    db = _get_db()
    return await db.get_stats()


@app.get("/health", summary="Health check")
async def health() -> dict[str, str]:
    """Verifica se a API está funcionando."""
    return {"status": "ok", "version": "0.1.0"}


# ── Fontes de Dados ──────────────────────────────────────────────────────────


@app.get("/municipios/{codigo_ibge}/fontes", summary="Fontes de um município")
async def list_fontes(codigo_ibge: str) -> dict[str, Any]:
    """Lista as fontes de dados de um município."""
    db = _get_db()
    fontes = await db.get_fontes_municipio(codigo_ibge)
    return {"total": len(fontes), "fontes": fontes}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
