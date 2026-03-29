"""Cliente da API do Querido Diário para diários oficiais municipais."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from core.logger import get_logger

logger = get_logger("QueriDiarioClient")


class QueriDiarioClient:
    """Cliente assíncrono para a API do Querido Diário (queridodiario.ok.org.br)."""

    def __init__(self, base_url: str = "https://queridodiario.ok.org.br/api") -> None:
        self.base_url = base_url.rstrip("/")

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=16),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Faz uma requisição GET à API do Querido Diário."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{self.base_url}{path}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def check_coverage(self, codigo_ibge: str) -> dict[str, Any]:
        """Verifica se um município tem cobertura no Querido Diário.

        Args:
            codigo_ibge: Código IBGE de 7 dígitos do município.

        Returns:
            Dict com keys: covered (bool), territory_id, last_gazette_date, gazette_count.
        """
        try:
            data = await self._get(f"/cities/{codigo_ibge}")
            return {
                "covered": bool(data),
                "territory_id": data.get("territory_id", codigo_ibge),
                "territory_name": data.get("territory_name"),
                "state_code": data.get("state_code"),
                "last_gazette_date": data.get("last_gazette_date"),
                "gazette_count": data.get("gazette_count", 0),
            }
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 422):
                return {"covered": False, "territory_id": codigo_ibge, "gazette_count": 0}
            raise
        except Exception as e:
            logger.warning(f"Erro ao verificar cobertura do Querido Diário para {codigo_ibge}: {e}")
            return {"covered": False, "territory_id": codigo_ibge, "gazette_count": 0}

    async def list_covered_cities(self) -> list[dict[str, Any]]:
        """Retorna a lista de todos os municípios cobertos pelo Querido Diário.

        Returns:
            Lista de dicts com territory_id, territory_name, state_code.
        """
        try:
            data = await self._get("/cities")
            if isinstance(data, list):
                return data
            return data.get("cities", [])
        except Exception as e:
            logger.warning(f"Erro ao listar cidades do Querido Diário: {e}")
            return []

    async def search_gazettes(
        self,
        codigo_ibge: str,
        query: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        page: int = 0,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """Busca diários oficiais de um município.

        Args:
            codigo_ibge: Código IBGE do município.
            query: Termos de busca (ex: 'licitação pregão').
            start_date: Data inicial de publicação.
            end_date: Data final de publicação.
            page: Número da página (0-indexed).
            page_size: Itens por página.

        Returns:
            Dict com keys: total_gazettes, gazettes (list), page, page_size.
        """
        params: dict[str, Any] = {
            "territory_ids": codigo_ibge,
            "offset": page * page_size,
            "size": page_size,
        }
        if query:
            params["querystring"] = query
        if start_date:
            params["published_since"] = start_date.isoformat()
        if end_date:
            params["published_until"] = end_date.isoformat()

        try:
            data = await self._get("/gazettes", params=params)
            return {
                "total_gazettes": data.get("total_gazettes", 0),
                "gazettes": data.get("gazettes", []),
                "page": page,
                "page_size": page_size,
            }
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (404, 422):
                return {"total_gazettes": 0, "gazettes": [], "page": page, "page_size": page_size}
            raise

    async def get_gazette(self, gazette_id: str) -> dict[str, Any] | None:
        """Busca um diário oficial específico pelo ID.

        Args:
            gazette_id: ID do diário oficial no Querido Diário.

        Returns:
            Dict com dados do diário ou None se não encontrado.
        """
        try:
            data = await self._get(f"/gazettes/{gazette_id}")
            return data
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
