"""Agente Scout — Descoberta de fontes de dados públicos municipais."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from agents.base import BaseAgent
from agents.prompts.scout_prompts import SCOUT_SYSTEM_PROMPT
from config.settings import settings
from core.database import Database
from core.models import FonteDados, Municipio, StatusDescoberta, TipoFonte, TipoSistema
from integrations.querido_diario import QueriDiarioClient


_TIPO_MAP: dict[str, TipoFonte] = {
    "portal_transparencia": TipoFonte.PORTAL_TRANSPARENCIA,
    "diario_oficial": TipoFonte.DIARIO_OFICIAL,
    "portal_licitacoes": TipoFonte.PORTAL_LICITACOES,
    "querido_diario": TipoFonte.QUERIDO_DIARIO,
    "associacao_municipios": TipoFonte.ASSOCIACAO_MUNICIPIOS,
    "outro": TipoFonte.OUTRO,
}

_SISTEMA_MAP: dict[str, TipoSistema] = {
    "betha": TipoSistema.BETHA,
    "ipm": TipoSistema.IPM,
    "fiorilli": TipoSistema.FIORILLI,
    "elotech": TipoSistema.ELOTECH,
    "governa": TipoSistema.GOVERNA,
    "portal_facil": TipoSistema.PORTAL_FACIL,
    "custom": TipoSistema.CUSTOM,
}


class ScoutAgent(BaseAgent):
    """Agente de descoberta de fontes de dados de transparência municipal.

    Para cada município:
    1. Verifica cobertura no Querido Diário
    2. Usa web search para encontrar demais portais de transparência
    3. Salva as fontes encontradas no banco de dados
    """

    def __init__(self, db: Database, model: str | None = None) -> None:
        super().__init__(db, model or settings.LLM_MODEL)
        self.qd_client = QueriDiarioClient(base_url=settings.QUERIDO_DIARIO_API_URL)

    async def execute(self, municipio: Municipio, dry_run: bool = False) -> list[FonteDados]:  # type: ignore[override]
        """Descobre todas as fontes de dados públicos de um município.

        Args:
            municipio: Dados do município a pesquisar.
            dry_run: Se True, não persiste no banco de dados.

        Returns:
            Lista de FonteDados descobertas.
        """
        self.stats["processados"] += 1
        self.logger.info(
            f"[Scout] Iniciando descoberta para {municipio.nome} - {municipio.uf} ({municipio.codigo_ibge})"
        )

        fontes: list[FonteDados] = []

        # 1. Verificar cobertura no Querido Diário
        qd_cobertura = await self._check_querido_diario(municipio)
        if qd_cobertura:
            fontes.append(qd_cobertura)

        # 2. Web search para demais fontes
        fontes_web = await asyncio.to_thread(self._search_web_sources, municipio)
        fontes.extend(fontes_web)

        if not fontes:
            self.logger.warning(f"[Scout] Nenhuma fonte encontrada para {municipio.nome}")
            if not dry_run:
                await self.db.update_municipio_status(
                    municipio.codigo_ibge, StatusDescoberta.NAO_ENCONTRADO
                )
            self.stats["ignorados"] += 1
            return []

        # 3. Persistir no banco
        if not dry_run:
            for fonte in fontes:
                fonte_dict = fonte.model_dump()
                # Converter tipos enum para string
                fonte_dict["tipo"] = fonte.tipo.value
                fonte_dict["tipo_sistema"] = fonte.tipo_sistema.value
                fonte_dict["status_mapeamento"] = fonte.status_mapeamento.value
                fonte_dict.setdefault("ultima_coleta", None)
                await self.db.upsert_fonte(fonte_dict)

            await self.db.update_municipio_status(
                municipio.codigo_ibge, StatusDescoberta.ENCONTRADO
            )

        self.stats["sucesso"] += 1
        self.logger.info(
            f"[Scout] {len(fontes)} fonte(s) encontrada(s) para {municipio.nome}"
        )
        return fontes

    async def _check_querido_diario(self, municipio: Municipio) -> FonteDados | None:
        """Verifica cobertura no Querido Diário e retorna fonte se coberto."""
        try:
            cobertura = await self.qd_client.check_coverage(municipio.codigo_ibge)
            if cobertura.get("covered"):
                self.logger.info(
                    f"[Scout] {municipio.nome} coberto pelo Querido Diário "
                    f"({cobertura.get('gazette_count', 0)} diários)"
                )
                return FonteDados(
                    id=str(uuid.uuid4()),
                    municipio_ibge=municipio.codigo_ibge,
                    tipo=TipoFonte.QUERIDO_DIARIO,
                    url=f"https://queridodiario.ok.org.br/municipio/{municipio.codigo_ibge}",
                    tipo_sistema=TipoSistema.DESCONHECIDO,
                    notas=(
                        f"Coberto pelo Querido Diário. "
                        f"Último diário: {cobertura.get('last_gazette_date')}. "
                        f"Total: {cobertura.get('gazette_count', 0)} edições."
                    ),
                )
        except Exception as e:
            self.logger.warning(f"[Scout] Erro ao verificar Querido Diário: {e}")
        return None

    def _search_web_sources(self, municipio: Municipio) -> list[FonteDados]:
        """Usa o LLM com web search para encontrar fontes do município.

        Execução síncrona (chamada via asyncio.to_thread pelo execute()).
        """
        user_message = (
            f"Encontre todas as fontes de dados públicos do município: "
            f"{municipio.nome}, {municipio.uf} (código IBGE: {municipio.codigo_ibge})"
        )

        try:
            # Usar web_search tool do Claude
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SCOUT_SYSTEM_PROMPT,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],  # type: ignore[list-item]
                messages=[{"role": "user", "content": user_message}],
            )

            # Continuar o loop até obter resposta final com JSON
            messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
            messages.append({"role": "assistant", "content": response.content})

            # Loop de tool use (web search é executada internamente pelo Claude)
            max_iter = 5
            final_text = ""
            for _ in range(max_iter):
                if response.stop_reason == "end_turn":
                    for block in response.content:
                        if hasattr(block, "text"):
                            final_text = block.text
                    break
                elif response.stop_reason == "tool_use":
                    # Claude executa a web search internamente e continua
                    response = self.client.messages.create(
                        model=self.model,
                        max_tokens=4096,
                        system=SCOUT_SYSTEM_PROMPT,
                        tools=[{"type": "web_search_20250305", "name": "web_search"}],  # type: ignore[list-item]
                        messages=messages,
                    )
                    messages.append({"role": "assistant", "content": response.content})
                else:
                    for block in response.content:
                        if hasattr(block, "text"):
                            final_text = block.text
                    break

            if not final_text:
                self.logger.warning(f"[Scout] Resposta vazia do LLM para {municipio.nome}")
                return []

            data = self.parse_json_response(final_text)
            return self._parse_fontes(data, municipio.codigo_ibge)

        except Exception as e:
            self.logger.error(f"[Scout] Erro na busca web para {municipio.nome}: {e}")
            self.stats["erro"] += 1
            return []

    def _parse_fontes(
        self, data: dict[str, Any], municipio_ibge: str
    ) -> list[FonteDados]:
        """Converte a resposta JSON do LLM em objetos FonteDados."""
        fontes: list[FonteDados] = []
        for item in data.get("fontes", []):
            url = item.get("url", "").strip()
            if not url or not url.startswith("http"):
                continue

            tipo_str = item.get("tipo", "outro").lower()
            tipo = _TIPO_MAP.get(tipo_str, TipoFonte.OUTRO)

            sistema_str = (item.get("sistema_identificado") or "desconhecido").lower()
            tipo_sistema = _SISTEMA_MAP.get(sistema_str, TipoSistema.DESCONHECIDO)

            fontes.append(
                FonteDados(
                    id=str(uuid.uuid4()),
                    municipio_ibge=municipio_ibge,
                    tipo=tipo,
                    url=url,
                    tipo_sistema=tipo_sistema,
                    notas=item.get("notas"),
                )
            )
        return fontes
