"""Testes para os módulos de integração (IBGE, Querido Diário, CNPJ)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, patch

from integrations.cnpj import clean_cnpj, format_cnpj, validate_cnpj


# ── Testes de CNPJ ────────────────────────────────────────────────────────


class TestCNPJ:
    def test_valid_cnpj(self):
        # CNPJs válidos conhecidos (empresas públicas)
        assert validate_cnpj("00.000.000/0001-91") is True  # Banco do Brasil
        assert validate_cnpj("00000000000191") is True

    def test_invalid_cnpj_wrong_digits(self):
        assert validate_cnpj("12345678000100") is False
        assert validate_cnpj("11111111111111") is False

    def test_invalid_cnpj_all_same(self):
        assert validate_cnpj("00000000000000") is False
        assert validate_cnpj("11111111111111") is False
        assert validate_cnpj("99999999999999") is False

    def test_invalid_cnpj_wrong_length(self):
        assert validate_cnpj("1234567") is False
        assert validate_cnpj("") is False
        assert validate_cnpj("123456789012345") is False

    def test_clean_cnpj(self):
        assert clean_cnpj("00.000.000/0001-91") == "00000000000191"
        assert clean_cnpj("00000000000191") == "00000000000191"
        assert clean_cnpj("  00.000.000/0001-91  ") == "00000000000191"

    def test_format_cnpj(self):
        assert format_cnpj("00000000000191") == "00.000.000/0001-91"
        assert format_cnpj("00.000.000/0001-91") == "00.000.000/0001-91"

    def test_format_cnpj_invalid_length(self):
        result = format_cnpj("123")
        assert result == "123"  # Retorna original


# ── Testes de IBGE Client ─────────────────────────────────────────────────


class TestIBGEClient:
    @pytest.mark.asyncio
    async def test_get_municipios_by_uf(self):
        """Testa parsing da resposta da API IBGE."""
        from integrations.ibge import IBGEClient

        mock_response = [
            {
                "id": 3550308,
                "nome": "São Paulo",
                "microrregiao": {
                    "mesorregiao": {
                        "UF": {"sigla": "SP"}
                    }
                },
            },
            {
                "id": 3509502,
                "nome": "Campinas",
                "microrregiao": {
                    "mesorregiao": {
                        "UF": {"sigla": "SP"}
                    }
                },
            },
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = AsyncMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = lambda: None
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )

            client = IBGEClient()
            result = await client.get_municipios_by_uf("SP")

        assert len(result) == 2
        assert result[0]["codigo_ibge"] == "3550308"
        assert result[0]["nome"] == "São Paulo"
        assert result[0]["uf"] == "SP"

    @pytest.mark.asyncio
    async def test_get_municipio_not_found(self):
        """Testa retorno None quando município não é encontrado."""
        import httpx
        from integrations.ibge import IBGEClient

        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = AsyncMock()
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=AsyncMock(),
                response=AsyncMock(status_code=404),
            )
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )

            client = IBGEClient()
            result = await client.get_municipio("9999999")

        assert result is None


# ── Testes do Querido Diário ──────────────────────────────────────────────


class TestQueriDiarioClient:
    @pytest.mark.asyncio
    async def test_check_coverage_found(self):
        """Testa cobertura quando município é coberto."""
        from integrations.querido_diario import QueriDiarioClient

        mock_response = {
            "territory_id": "3550308",
            "territory_name": "São Paulo",
            "state_code": "SP",
            "last_gazette_date": "2024-03-15",
            "gazette_count": 1500,
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = AsyncMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = lambda: None
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )

            client = QueriDiarioClient()
            result = await client.check_coverage("3550308")

        assert result["covered"] is True
        assert result["gazette_count"] == 1500

    @pytest.mark.asyncio
    async def test_check_coverage_not_found(self):
        """Testa cobertura quando município não é coberto."""
        import httpx
        from integrations.querido_diario import QueriDiarioClient

        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = AsyncMock()
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=AsyncMock(),
                response=AsyncMock(status_code=404),
            )
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )

            client = QueriDiarioClient()
            result = await client.check_coverage("9999999")

        assert result["covered"] is False
