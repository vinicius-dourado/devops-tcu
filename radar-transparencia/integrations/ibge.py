"""Cliente da API do IBGE para dados de municípios brasileiros."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from core.logger import get_logger

logger = get_logger("IBGEClient")

IBGE_API_BASE = "https://servicodados.ibge.gov.br/api/v1"


class IBGEClient:
    """Cliente assíncrono para a API de localidades do IBGE."""

    def __init__(self, base_url: str = IBGE_API_BASE, timeout: float = 30.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=16),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Faz uma requisição GET à API do IBGE."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}{path}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_all_municipios(self) -> list[dict[str, Any]]:
        """Retorna todos os municípios brasileiros com código IBGE, nome e UF.

        Returns:
            Lista de dicts com keys: codigo_ibge, nome, uf.
        """
        logger.info("Buscando todos os municípios do IBGE...")
        data = await self._get(
            "/localidades/municipios",
            params={"orderBy": "nome"},
        )
        municipios = []
        for item in data:
            municipios.append(
                {
                    "codigo_ibge": str(item["id"]),
                    "nome": item["nome"],
                    "uf": item["microrregiao"]["mesorregiao"]["UF"]["sigla"],
                    "populacao": None,
                }
            )
        logger.info(f"Total de municípios encontrados: {len(municipios)}")
        return municipios

    async def get_municipios_by_uf(self, uf: str) -> list[dict[str, Any]]:
        """Retorna todos os municípios de um estado.

        Args:
            uf: Sigla do estado (ex: 'SP', 'MG').

        Returns:
            Lista de dicts com keys: codigo_ibge, nome, uf.
        """
        logger.info(f"Buscando municípios do estado {uf}...")
        data = await self._get(
            f"/localidades/estados/{uf}/municipios",
            params={"orderBy": "nome"},
        )
        municipios = [
            {
                "codigo_ibge": str(item["id"]),
                "nome": item["nome"],
                "uf": uf.upper(),
                "populacao": None,
            }
            for item in data
        ]
        logger.info(f"Municípios em {uf}: {len(municipios)}")
        return municipios

    async def get_municipio(self, codigo_ibge: str) -> dict[str, Any] | None:
        """Busca informações de um município pelo código IBGE.

        Args:
            codigo_ibge: Código IBGE de 7 dígitos.

        Returns:
            Dict com dados do município ou None se não encontrado.
        """
        try:
            data = await self._get(f"/localidades/municipios/{codigo_ibge}")
            if not data:
                return None
            return {
                "codigo_ibge": str(data["id"]),
                "nome": data["nome"],
                "uf": data["microrregiao"]["mesorregiao"]["UF"]["sigla"],
                "populacao": None,
            }
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_estados(self) -> list[dict[str, Any]]:
        """Retorna a lista de estados brasileiros.

        Returns:
            Lista de dicts com keys: id, sigla, nome.
        """
        data = await self._get("/localidades/estados", params={"orderBy": "nome"})
        return [{"id": item["id"], "sigla": item["sigla"], "nome": item["nome"]} for item in data]
