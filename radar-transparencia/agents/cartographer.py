"""Agente Cartógrafo — Mapeamento de estrutura de portais de transparência."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.base import BaseAgent
from agents.prompts.cartographer_prompts import CARTOGRAPHER_SYSTEM_PROMPT
from config.settings import settings
from core.database import Database
from core.models import FonteDados, StatusMapeamento, TipoSistema


_SISTEMA_MAP: dict[str, TipoSistema] = {
    "betha": TipoSistema.BETHA,
    "ipm": TipoSistema.IPM,
    "fiorilli": TipoSistema.FIORILLI,
    "elotech": TipoSistema.ELOTECH,
    "governa": TipoSistema.GOVERNA,
    "portal_facil": TipoSistema.PORTAL_FACIL,
    "custom": TipoSistema.CUSTOM,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


class CartographerAgent(BaseAgent):
    """Agente de mapeamento de estrutura de portais de transparência.

    Para cada fonte de dados:
    1. Verifica se há mapa reutilizável para o sistema já identificado
    2. Busca o HTML do portal (httpx ou playwright se JS)
    3. Envia ao LLM para gerar o roteiro de coleta
    4. Valida o roteiro testando-o uma vez
    5. Salva o mapa de navegação no banco
    """

    def __init__(self, db: Database, model: str | None = None) -> None:
        super().__init__(db, model or settings.LLM_MODEL)

    async def execute(self, fonte: FonteDados, dry_run: bool = False) -> dict[str, Any]:  # type: ignore[override]
        """Mapeia a estrutura de um portal de transparência.

        Args:
            fonte: Fonte de dados a mapear.
            dry_run: Se True, não persiste no banco.

        Returns:
            Mapa de navegação gerado pelo LLM.
        """
        self.stats["processados"] += 1
        self.logger.info(f"[Cartógrafo] Mapeando {fonte.url}")

        # 1. Reutilizar mapa existente para o mesmo sistema
        if fonte.tipo_sistema not in (TipoSistema.DESCONHECIDO, TipoSistema.CUSTOM):
            existing = await self.db.get_fonte_by_sistema(fonte.tipo_sistema.value)
            if existing and existing.get("mapa_navegacao"):
                self.logger.info(
                    f"[Cartógrafo] Reutilizando mapa para sistema {fonte.tipo_sistema.value}"
                )
                mapa = existing["mapa_navegacao"]
                if not dry_run:
                    await self._save_mapa(fonte, mapa, reutilizado=True)
                self.stats["sucesso"] += 1
                return mapa

        # 2. Buscar conteúdo do portal
        html, network_log = await self._fetch_portal(fonte.url)
        if not html:
            self.logger.warning(f"[Cartógrafo] Não foi possível acessar {fonte.url}")
            if not dry_run:
                await self._update_status(fonte, StatusMapeamento.INACESSIVEL)
            self.stats["erro"] += 1
            return {}

        # 3. Gerar mapa via LLM
        mapa = await asyncio.to_thread(self._analyze_with_llm, fonte.url, html, network_log)
        if not mapa:
            if not dry_run:
                await self._update_status(fonte, StatusMapeamento.ERRO)
            self.stats["erro"] += 1
            return {}

        # 4. Atualizar tipo de sistema se identificado
        sistema_str = (mapa.get("sistema_identificado") or "desconhecido").lower()
        tipo_sistema = _SISTEMA_MAP.get(sistema_str, TipoSistema.DESCONHECIDO)
        if tipo_sistema != TipoSistema.DESCONHECIDO:
            fonte.tipo_sistema = tipo_sistema

        # 5. Validar roteiro (opcional, tenta 1 vez)
        mapa["roteiro_validado"] = await self._validate_roteiro(mapa)

        # 6. Persistir
        if not dry_run:
            await self._save_mapa(fonte, mapa)

        self.stats["sucesso"] += 1
        self.logger.info(
            f"[Cartógrafo] Mapa gerado para {fonte.url} "
            f"(sistema: {sistema_str}, validado: {mapa.get('roteiro_validado')})"
        )
        return mapa

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=False,
    )
    async def _fetch_portal(self, url: str) -> tuple[str, list[dict[str, Any]]]:
        """Busca o HTML de um portal. Tenta playwright se USE_PLAYWRIGHT=true.

        Returns:
            Tuple (html, network_requests_log).
        """
        if settings.USE_PLAYWRIGHT:
            return await self._fetch_with_playwright(url)
        return await self._fetch_with_httpx(url), []

    async def _fetch_with_httpx(self, url: str) -> str:
        """Busca HTML estático com httpx."""
        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                headers=HEADERS,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as e:
            self.logger.warning(f"[Cartógrafo] httpx falhou para {url}: {e}")
            return ""

    async def _fetch_with_playwright(
        self, url: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """Busca HTML renderizado via playwright com interceptação de rede."""
        try:
            from playwright.async_api import async_playwright  # type: ignore[import]
        except ImportError:
            self.logger.warning(
                "[Cartógrafo] playwright não instalado. Usando httpx."
                " Instale com: pip install 'radar-transparencia[browser]' && playwright install chromium"
            )
            return await self._fetch_with_httpx(url), []

        network_log: list[dict[str, Any]] = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    locale="pt-BR",
                )
                page = await context.new_page()

                # Interceptar requisições de rede (AJAX/XHR/fetch)
                async def on_request(request: Any) -> None:
                    if request.resource_type in ("xhr", "fetch"):
                        network_log.append(
                            {
                                "url": request.url,
                                "method": request.method,
                                "resource_type": request.resource_type,
                                "headers": dict(request.headers),
                                "post_data": request.post_data,
                            }
                        )

                async def on_response(response: Any) -> None:
                    if response.request.resource_type in ("xhr", "fetch"):
                        for entry in network_log:
                            if entry["url"] == response.url and "response_status" not in entry:
                                entry["response_status"] = response.status
                                entry["response_content_type"] = response.headers.get(
                                    "content-type", ""
                                )
                                try:
                                    if "json" in entry["response_content_type"]:
                                        body = await response.text()
                                        entry["response_preview"] = body[:500]
                                except Exception:
                                    pass
                                break

                page.on("request", on_request)
                page.on("response", on_response)

                await page.goto(url, wait_until="networkidle", timeout=30000)
                # Aguardar um pouco mais para carregamentos lentos
                await asyncio.sleep(2)

                html = await page.content()
                await browser.close()

                return html, network_log[:50]  # Limitar log a 50 entradas

        except Exception as e:
            self.logger.warning(f"[Cartógrafo] playwright falhou para {url}: {e}")
            html = await self._fetch_with_httpx(url)
            return html, []

    def _analyze_with_llm(
        self,
        url: str,
        html: str,
        network_log: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Envia HTML ao LLM para análise e geração do mapa de navegação."""
        # Truncar HTML para não exceder context window
        html_truncated = html[:15000] if len(html) > 15000 else html

        network_summary = ""
        if network_log:
            network_summary = (
                "\n\n## Requisições de rede interceptadas (AJAX/XHR/fetch):\n"
                + json.dumps(network_log[:20], ensure_ascii=False, indent=2)
            )

        user_message = (
            f"Analise o portal de transparência em: {url}\n\n"
            f"## HTML da página (primeiros 15.000 caracteres):\n{html_truncated}"
            f"{network_summary}"
        )

        text = self.call_llm(CARTOGRAPHER_SYSTEM_PROMPT, user_message, max_tokens=8096)
        return self.parse_json_response(text)

    async def _validate_roteiro(self, mapa: dict[str, Any]) -> bool:
        """Valida o roteiro tentando seguir o primeiro passo de cada roteiro."""
        roteiros = mapa.get("roteiros_coleta", [])
        if not roteiros:
            return False

        # Pegar o roteiro de maior prioridade
        roteiro = roteiros[0]
        passos = roteiro.get("passos", [])
        if not passos:
            return False

        primeiro_passo = passos[0]
        url_teste = primeiro_passo.get("url", "")
        metodo = primeiro_passo.get("metodo", "GET").upper()

        if not url_teste or "{" in url_teste:
            # URL tem placeholders, difícil validar automaticamente
            return False

        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers=HEADERS, follow_redirects=True
            ) as client:
                if metodo == "GET":
                    resp = await client.get(url_teste)
                elif metodo == "POST":
                    body = primeiro_passo.get("body", "")
                    resp = await client.post(url_teste, data=body)
                else:
                    return False

                return resp.status_code < 400

        except Exception as e:
            self.logger.debug(f"[Cartógrafo] Validação falhou: {e}")
            return False

    async def _save_mapa(
        self,
        fonte: FonteDados,
        mapa: dict[str, Any],
        reutilizado: bool = False,
    ) -> None:
        """Persiste o mapa de navegação no banco."""
        status = StatusMapeamento.MAPEADO if mapa else StatusMapeamento.ERRO
        fonte_dict: dict[str, Any] = {
            "id": fonte.id,
            "municipio_ibge": fonte.municipio_ibge,
            "tipo": fonte.tipo.value,
            "url": fonte.url,
            "tipo_sistema": fonte.tipo_sistema.value,
            "status_mapeamento": status.value,
            "mapa_navegacao": mapa,
            "ultima_coleta": datetime.now().isoformat(),
            "notas": (
                fonte.notas or ""
            ) + (" [mapa reutilizado]" if reutilizado else ""),
        }
        await self.db.upsert_fonte(fonte_dict)

    async def _update_status(
        self, fonte: FonteDados, status: StatusMapeamento
    ) -> None:
        """Atualiza apenas o status de mapeamento da fonte."""
        fonte_dict: dict[str, Any] = {
            "id": fonte.id,
            "municipio_ibge": fonte.municipio_ibge,
            "tipo": fonte.tipo.value,
            "url": fonte.url,
            "tipo_sistema": fonte.tipo_sistema.value,
            "status_mapeamento": status.value,
            "mapa_navegacao": None,
            "ultima_coleta": None,
            "notas": fonte.notas,
        }
        await self.db.upsert_fonte(fonte_dict)
