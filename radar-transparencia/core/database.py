"""Conexão e setup do banco de dados SQLite para o Radar Transparência."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator

import aiosqlite

from core.logger import get_logger

logger = get_logger("Database")

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS municipios (
    codigo_ibge TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    uf TEXT NOT NULL,
    populacao INTEGER,
    status_descoberta TEXT NOT NULL DEFAULT 'pendente',
    ultima_atualizacao TEXT
);

CREATE TABLE IF NOT EXISTS fontes_dados (
    id TEXT PRIMARY KEY,
    municipio_ibge TEXT NOT NULL,
    tipo TEXT NOT NULL,
    url TEXT NOT NULL,
    tipo_sistema TEXT NOT NULL DEFAULT 'desconhecido',
    status_mapeamento TEXT NOT NULL DEFAULT 'pendente',
    mapa_navegacao TEXT,
    ultima_coleta TEXT,
    notas TEXT,
    FOREIGN KEY (municipio_ibge) REFERENCES municipios(codigo_ibge)
);

CREATE TABLE IF NOT EXISTS licitacoes (
    id TEXT PRIMARY KEY,
    municipio_ibge TEXT NOT NULL,
    fonte_id TEXT NOT NULL,
    numero TEXT,
    modalidade TEXT,
    objeto TEXT NOT NULL,
    valor_estimado REAL,
    valor_contratado REAL,
    data_abertura TEXT,
    data_publicacao TEXT,
    situacao TEXT,
    vencedor_nome TEXT,
    vencedor_cnpj TEXT,
    url_origem TEXT,
    texto_original TEXT,
    confianca_extracao REAL NOT NULL DEFAULT 0.0,
    validado INTEGER NOT NULL DEFAULT 0,
    data_coleta TEXT NOT NULL,
    FOREIGN KEY (municipio_ibge) REFERENCES municipios(codigo_ibge),
    FOREIGN KEY (fonte_id) REFERENCES fontes_dados(id)
);

CREATE TABLE IF NOT EXISTS contratos (
    id TEXT PRIMARY KEY,
    municipio_ibge TEXT NOT NULL,
    fonte_id TEXT NOT NULL,
    numero TEXT,
    licitacao_numero TEXT,
    objeto TEXT NOT NULL,
    contratado_nome TEXT,
    contratado_cnpj TEXT,
    valor REAL,
    data_assinatura TEXT,
    data_inicio TEXT,
    data_fim TEXT,
    url_origem TEXT,
    texto_original TEXT,
    confianca_extracao REAL NOT NULL DEFAULT 0.0,
    validado INTEGER NOT NULL DEFAULT 0,
    data_coleta TEXT NOT NULL,
    FOREIGN KEY (municipio_ibge) REFERENCES municipios(codigo_ibge),
    FOREIGN KEY (fonte_id) REFERENCES fontes_dados(id)
);

CREATE TABLE IF NOT EXISTS publicacoes_diario (
    id TEXT PRIMARY KEY,
    municipio_ibge TEXT NOT NULL,
    fonte_id TEXT NOT NULL,
    data_publicacao TEXT NOT NULL,
    tipo_ato TEXT,
    ementa TEXT,
    texto_completo TEXT,
    url_origem TEXT,
    arquivo_original TEXT,
    confianca_extracao REAL NOT NULL DEFAULT 0.0,
    data_coleta TEXT NOT NULL,
    FOREIGN KEY (municipio_ibge) REFERENCES municipios(codigo_ibge),
    FOREIGN KEY (fonte_id) REFERENCES fontes_dados(id)
);

CREATE TABLE IF NOT EXISTS anomalias (
    id TEXT PRIMARY KEY,
    municipio_ibge TEXT NOT NULL,
    tipo TEXT NOT NULL,
    descricao TEXT NOT NULL,
    severidade TEXT NOT NULL,
    dados_referencia TEXT NOT NULL DEFAULT '{}',
    data_deteccao TEXT NOT NULL,
    FOREIGN KEY (municipio_ibge) REFERENCES municipios(codigo_ibge)
);

CREATE TABLE IF NOT EXISTS execucoes_log (
    id TEXT PRIMARY KEY,
    municipio_ibge TEXT NOT NULL,
    etapa TEXT NOT NULL,
    status TEXT NOT NULL,
    mensagem TEXT,
    iniciado_em TEXT NOT NULL,
    finalizado_em TEXT,
    detalhes TEXT,
    FOREIGN KEY (municipio_ibge) REFERENCES municipios(codigo_ibge)
);

CREATE INDEX IF NOT EXISTS idx_fontes_municipio ON fontes_dados(municipio_ibge);
CREATE INDEX IF NOT EXISTS idx_licitacoes_municipio ON licitacoes(municipio_ibge);
CREATE INDEX IF NOT EXISTS idx_contratos_municipio ON contratos(municipio_ibge);
CREATE INDEX IF NOT EXISTS idx_publicacoes_municipio ON publicacoes_diario(municipio_ibge);
CREATE INDEX IF NOT EXISTS idx_anomalias_municipio ON anomalias(municipio_ibge);
CREATE INDEX IF NOT EXISTS idx_execucoes_municipio ON execucoes_log(municipio_ibge);
"""


class Database:
    """Gerenciador de banco de dados SQLite assíncrono."""

    def __init__(self, db_path: str = "radar_transparencia.db") -> None:
        """Inicializa o gerenciador de banco de dados.

        Args:
            db_path: Caminho para o arquivo SQLite.
        """
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Abre a conexão com o banco e cria as tabelas se necessário."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(CREATE_TABLES_SQL)
        await self._conn.commit()
        logger.info(f"Banco de dados conectado: {self.db_path}")

    async def close(self) -> None:
        """Fecha a conexão com o banco."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Context manager para transações."""
        if not self._conn:
            raise RuntimeError("Database not connected. Call connect() first.")
        try:
            yield self._conn
            await self._conn.commit()
        except Exception:
            await self._conn.rollback()
            raise

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """Executa uma query SQL."""
        if not self._conn:
            raise RuntimeError("Database not connected.")
        return await self._conn.execute(sql, params)

    async def executemany(self, sql: str, params_list: list[tuple]) -> None:
        """Executa uma query SQL para múltiplos conjuntos de parâmetros."""
        if not self._conn:
            raise RuntimeError("Database not connected.")
        await self._conn.executemany(sql, params_list)
        await self._conn.commit()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Executa uma query e retorna todos os resultados como lista de dicts."""
        cursor = await self.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Executa uma query e retorna o primeiro resultado como dict."""
        cursor = await self.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ── Municipios ──────────────────────────────────────────────────────────

    async def upsert_municipio(self, municipio: dict[str, Any]) -> None:
        """Insere ou atualiza um município."""
        async with self.transaction():
            await self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT INTO municipios (codigo_ibge, nome, uf, populacao, status_descoberta, ultima_atualizacao)
                VALUES (:codigo_ibge, :nome, :uf, :populacao, :status_descoberta, :ultima_atualizacao)
                ON CONFLICT(codigo_ibge) DO UPDATE SET
                    nome = excluded.nome,
                    uf = excluded.uf,
                    populacao = COALESCE(excluded.populacao, municipios.populacao),
                    status_descoberta = excluded.status_descoberta,
                    ultima_atualizacao = excluded.ultima_atualizacao
                """,
                municipio,
            )

    async def get_municipio(self, codigo_ibge: str) -> dict[str, Any] | None:
        """Busca um município pelo código IBGE."""
        return await self.fetchone(
            "SELECT * FROM municipios WHERE codigo_ibge = ?", (codigo_ibge,)
        )

    async def list_municipios(
        self,
        uf: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lista municípios com filtros opcionais."""
        conditions = []
        params: list[Any] = []
        if uf:
            conditions.append("uf = ?")
            params.append(uf)
        if status:
            conditions.append("status_descoberta = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = f"LIMIT {limit}" if limit else ""
        offset_clause = f"OFFSET {offset}" if offset else ""
        return await self.fetchall(
            f"SELECT * FROM municipios {where} ORDER BY nome {limit_clause} {offset_clause}",
            tuple(params),
        )

    async def update_municipio_status(
        self, codigo_ibge: str, status: str
    ) -> None:
        """Atualiza o status de descoberta de um município."""
        async with self.transaction():
            await self._conn.execute(  # type: ignore[union-attr]
                "UPDATE municipios SET status_descoberta = ?, ultima_atualizacao = ? WHERE codigo_ibge = ?",
                (status, datetime.now().isoformat(), codigo_ibge),
            )

    # ── Fontes de Dados ──────────────────────────────────────────────────────

    async def upsert_fonte(self, fonte: dict[str, Any]) -> str:
        """Insere ou atualiza uma fonte de dados. Retorna o id."""
        if not fonte.get("id"):
            fonte["id"] = str(uuid.uuid4())
        mapa = fonte.get("mapa_navegacao")
        if isinstance(mapa, dict):
            fonte = dict(fonte)
            fonte["mapa_navegacao"] = json.dumps(mapa, ensure_ascii=False)
        async with self.transaction():
            await self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT INTO fontes_dados (id, municipio_ibge, tipo, url, tipo_sistema, status_mapeamento, mapa_navegacao, ultima_coleta, notas)
                VALUES (:id, :municipio_ibge, :tipo, :url, :tipo_sistema, :status_mapeamento, :mapa_navegacao, :ultima_coleta, :notas)
                ON CONFLICT(id) DO UPDATE SET
                    tipo = excluded.tipo,
                    url = excluded.url,
                    tipo_sistema = excluded.tipo_sistema,
                    status_mapeamento = excluded.status_mapeamento,
                    mapa_navegacao = COALESCE(excluded.mapa_navegacao, fontes_dados.mapa_navegacao),
                    ultima_coleta = COALESCE(excluded.ultima_coleta, fontes_dados.ultima_coleta),
                    notas = COALESCE(excluded.notas, fontes_dados.notas)
                """,
                fonte,
            )
        return fonte["id"]

    async def get_fontes_municipio(
        self, municipio_ibge: str
    ) -> list[dict[str, Any]]:
        """Retorna todas as fontes de um município."""
        rows = await self.fetchall(
            "SELECT * FROM fontes_dados WHERE municipio_ibge = ?",
            (municipio_ibge,),
        )
        for row in rows:
            if row.get("mapa_navegacao"):
                try:
                    row["mapa_navegacao"] = json.loads(row["mapa_navegacao"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return rows

    async def get_fonte_by_sistema(
        self, tipo_sistema: str
    ) -> dict[str, Any] | None:
        """Busca uma fonte já mapeada pelo tipo de sistema (para reutilização)."""
        row = await self.fetchone(
            "SELECT * FROM fontes_dados WHERE tipo_sistema = ? AND status_mapeamento = 'mapeado' AND mapa_navegacao IS NOT NULL LIMIT 1",
            (tipo_sistema,),
        )
        if row and row.get("mapa_navegacao"):
            try:
                row["mapa_navegacao"] = json.loads(row["mapa_navegacao"])
            except (json.JSONDecodeError, TypeError):
                pass
        return row

    # ── Licitações ──────────────────────────────────────────────────────────

    async def insert_licitacao(self, licitacao: dict[str, Any]) -> str:
        """Insere uma licitação. Retorna o id."""
        if not licitacao.get("id"):
            licitacao = dict(licitacao)
            licitacao["id"] = str(uuid.uuid4())
        async with self.transaction():
            await self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT OR REPLACE INTO licitacoes
                (id, municipio_ibge, fonte_id, numero, modalidade, objeto, valor_estimado,
                 valor_contratado, data_abertura, data_publicacao, situacao, vencedor_nome,
                 vencedor_cnpj, url_origem, texto_original, confianca_extracao, validado, data_coleta)
                VALUES
                (:id, :municipio_ibge, :fonte_id, :numero, :modalidade, :objeto, :valor_estimado,
                 :valor_contratado, :data_abertura, :data_publicacao, :situacao, :vencedor_nome,
                 :vencedor_cnpj, :url_origem, :texto_original, :confianca_extracao, :validado, :data_coleta)
                """,
                licitacao,
            )
        return licitacao["id"]

    async def list_licitacoes(
        self,
        municipio_ibge: str | None = None,
        validado: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lista licitações com filtros opcionais."""
        conditions = []
        params: list[Any] = []
        if municipio_ibge:
            conditions.append("municipio_ibge = ?")
            params.append(municipio_ibge)
        if validado is not None:
            conditions.append("validado = ?")
            params.append(1 if validado else 0)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        return await self.fetchall(
            f"SELECT * FROM licitacoes {where} ORDER BY data_coleta DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    # ── Contratos ────────────────────────────────────────────────────────────

    async def insert_contrato(self, contrato: dict[str, Any]) -> str:
        """Insere um contrato. Retorna o id."""
        if not contrato.get("id"):
            contrato = dict(contrato)
            contrato["id"] = str(uuid.uuid4())
        async with self.transaction():
            await self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT OR REPLACE INTO contratos
                (id, municipio_ibge, fonte_id, numero, licitacao_numero, objeto,
                 contratado_nome, contratado_cnpj, valor, data_assinatura, data_inicio,
                 data_fim, url_origem, texto_original, confianca_extracao, validado, data_coleta)
                VALUES
                (:id, :municipio_ibge, :fonte_id, :numero, :licitacao_numero, :objeto,
                 :contratado_nome, :contratado_cnpj, :valor, :data_assinatura, :data_inicio,
                 :data_fim, :url_origem, :texto_original, :confianca_extracao, :validado, :data_coleta)
                """,
                contrato,
            )
        return contrato["id"]

    async def list_contratos(
        self,
        municipio_ibge: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Lista contratos com filtros opcionais."""
        conditions = []
        params: list[Any] = []
        if municipio_ibge:
            conditions.append("municipio_ibge = ?")
            params.append(municipio_ibge)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        return await self.fetchall(
            f"SELECT * FROM contratos {where} ORDER BY data_coleta DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

    # ── Publicações de Diário ────────────────────────────────────────────────

    async def insert_publicacao(self, publicacao: dict[str, Any]) -> str:
        """Insere uma publicação de diário oficial. Retorna o id."""
        if not publicacao.get("id"):
            publicacao = dict(publicacao)
            publicacao["id"] = str(uuid.uuid4())
        async with self.transaction():
            await self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT OR REPLACE INTO publicacoes_diario
                (id, municipio_ibge, fonte_id, data_publicacao, tipo_ato, ementa,
                 texto_completo, url_origem, arquivo_original, confianca_extracao, data_coleta)
                VALUES
                (:id, :municipio_ibge, :fonte_id, :data_publicacao, :tipo_ato, :ementa,
                 :texto_completo, :url_origem, :arquivo_original, :confianca_extracao, :data_coleta)
                """,
                publicacao,
            )
        return publicacao["id"]

    # ── Anomalias ────────────────────────────────────────────────────────────

    async def insert_anomalia(self, anomalia: dict[str, Any]) -> str:
        """Insere uma anomalia detectada. Retorna o id."""
        if not anomalia.get("id"):
            anomalia = dict(anomalia)
            anomalia["id"] = str(uuid.uuid4())
        dados = anomalia.get("dados_referencia", {})
        if isinstance(dados, dict):
            anomalia = dict(anomalia)
            anomalia["dados_referencia"] = json.dumps(dados, ensure_ascii=False)
        async with self.transaction():
            await self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT OR REPLACE INTO anomalias
                (id, municipio_ibge, tipo, descricao, severidade, dados_referencia, data_deteccao)
                VALUES
                (:id, :municipio_ibge, :tipo, :descricao, :severidade, :dados_referencia, :data_deteccao)
                """,
                anomalia,
            )
        return anomalia["id"]

    async def list_anomalias(
        self,
        municipio_ibge: str | None = None,
        severidade: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lista anomalias ordenadas por severidade."""
        conditions = []
        params: list[Any] = []
        if municipio_ibge:
            conditions.append("municipio_ibge = ?")
            params.append(municipio_ibge)
        if severidade:
            conditions.append("severidade = ?")
            params.append(severidade)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = await self.fetchall(
            f"""
            SELECT * FROM anomalias {where}
            ORDER BY CASE severidade
                WHEN 'critica' THEN 1 WHEN 'alta' THEN 2
                WHEN 'media' THEN 3 WHEN 'baixa' THEN 4 ELSE 5
            END, data_deteccao DESC LIMIT ?
            """,
            tuple(params),
        )
        for row in rows:
            if row.get("dados_referencia"):
                try:
                    row["dados_referencia"] = json.loads(row["dados_referencia"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return rows

    # ── Execuções Log ────────────────────────────────────────────────────────

    async def log_execucao(self, log: dict[str, Any]) -> str:
        """Registra uma execução de pipeline. Retorna o id."""
        if not log.get("id"):
            log = dict(log)
            log["id"] = str(uuid.uuid4())
        detalhes = log.get("detalhes")
        if isinstance(detalhes, dict):
            log = dict(log)
            log["detalhes"] = json.dumps(detalhes, ensure_ascii=False)
        async with self.transaction():
            await self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT OR REPLACE INTO execucoes_log
                (id, municipio_ibge, etapa, status, mensagem, iniciado_em, finalizado_em, detalhes)
                VALUES
                (:id, :municipio_ibge, :etapa, :status, :mensagem, :iniciado_em, :finalizado_em, :detalhes)
                """,
                log,
            )
        return log["id"]

    async def get_ultimo_log(
        self, municipio_ibge: str, etapa: str
    ) -> dict[str, Any] | None:
        """Retorna o último log de execução de uma etapa para um município."""
        return await self.fetchone(
            "SELECT * FROM execucoes_log WHERE municipio_ibge = ? AND etapa = ? ORDER BY iniciado_em DESC LIMIT 1",
            (municipio_ibge, etapa),
        )

    # ── Stats ────────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict[str, Any]:
        """Retorna estatísticas gerais do banco."""
        municipios = await self.fetchone("SELECT COUNT(*) as total FROM municipios")
        cobertos = await self.fetchone(
            "SELECT COUNT(*) as total FROM municipios WHERE status_descoberta = 'encontrado'"
        )
        licitacoes = await self.fetchone("SELECT COUNT(*) as total FROM licitacoes")
        contratos = await self.fetchone("SELECT COUNT(*) as total FROM contratos")
        anomalias = await self.fetchone("SELECT COUNT(*) as total FROM anomalias")
        return {
            "total_municipios": municipios["total"] if municipios else 0,
            "municipios_cobertos": cobertos["total"] if cobertos else 0,
            "total_licitacoes": licitacoes["total"] if licitacoes else 0,
            "total_contratos": contratos["total"] if contratos else 0,
            "total_anomalias": anomalias["total"] if anomalias else 0,
        }
