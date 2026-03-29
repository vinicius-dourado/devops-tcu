"""Testes para o Agente Scout."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.models import Municipio, StatusDescoberta, TipoFonte, TipoSistema


MOCK_SCOUT_RESPONSE = """{
  "municipio": "Campinas",
  "uf": "SP",
  "codigo_ibge": "3509502",
  "fontes": [
    {
      "tipo": "portal_transparencia",
      "url": "https://www.campinas.sp.gov.br/portal-transparencia",
      "sistema_identificado": "betha",
      "acessivel": true,
      "notas": "Portal de transparência da Prefeitura de Campinas"
    },
    {
      "tipo": "diario_oficial",
      "url": "https://www.campinas.sp.gov.br/diario-oficial",
      "sistema_identificado": "desconhecido",
      "acessivel": true,
      "notas": "Diário Oficial do Município de Campinas"
    }
  ],
  "cobertura_querido_diario": false,
  "observacoes_gerais": "Portal de transparência encontrado com sistema Betha"
}"""


class TestScoutAgent:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.upsert_fonte = AsyncMock()
        db.update_municipio_status = AsyncMock()
        db.get_fontes_municipio = AsyncMock(return_value=[])
        return db

    @pytest.fixture
    def municipio_campinas(self):
        return Municipio(
            codigo_ibge="3509502",
            nome="Campinas",
            uf="SP",
            populacao=1200000,
        )

    def test_parse_fontes_from_json(self, mock_db):
        """Testa parsing da resposta JSON do LLM."""
        from agents.scout import ScoutAgent

        with patch("anthropic.Anthropic"):
            agent = ScoutAgent(mock_db)

        data = {
            "fontes": [
                {
                    "tipo": "portal_transparencia",
                    "url": "https://example.com/transparencia",
                    "sistema_identificado": "betha",
                    "acessivel": True,
                    "notas": "Portal principal",
                },
                {
                    "tipo": "diario_oficial",
                    "url": "https://example.com/diario",
                    "sistema_identificado": "desconhecido",
                    "acessivel": True,
                    "notas": None,
                },
            ]
        }

        fontes = agent._parse_fontes(data, "3509502")

        assert len(fontes) == 2
        assert fontes[0].tipo == TipoFonte.PORTAL_TRANSPARENCIA
        assert fontes[0].tipo_sistema == TipoSistema.BETHA
        assert fontes[0].url == "https://example.com/transparencia"
        assert fontes[1].tipo == TipoFonte.DIARIO_OFICIAL
        assert fontes[1].tipo_sistema == TipoSistema.DESCONHECIDO

    def test_parse_fontes_ignores_invalid_urls(self, mock_db):
        """Testa que URLs inválidas são ignoradas."""
        from agents.scout import ScoutAgent

        with patch("anthropic.Anthropic"):
            agent = ScoutAgent(mock_db)

        data = {
            "fontes": [
                {"tipo": "portal_transparencia", "url": "", "sistema_identificado": "desconhecido"},
                {"tipo": "portal_transparencia", "url": "not-a-url", "sistema_identificado": "desconhecido"},
                {"tipo": "portal_transparencia", "url": "https://valid.com", "sistema_identificado": "ipm"},
            ]
        }

        fontes = agent._parse_fontes(data, "3509502")

        assert len(fontes) == 1  # Apenas a URL válida
        assert fontes[0].tipo_sistema == TipoSistema.IPM

    def test_parse_json_response_with_markdown(self, mock_db):
        """Testa extração de JSON de resposta com markdown."""
        from agents.base import BaseAgent
        from agents.scout import ScoutAgent

        with patch("anthropic.Anthropic"):
            agent = ScoutAgent(mock_db)

        # JSON dentro de bloco markdown
        text = f"```json\n{MOCK_SCOUT_RESPONSE}\n```"
        result = agent.parse_json_response(text)

        assert result["municipio"] == "Campinas"
        assert len(result["fontes"]) == 2

    def test_parse_json_response_plain(self, mock_db):
        """Testa extração de JSON puro."""
        from agents.scout import ScoutAgent

        with patch("anthropic.Anthropic"):
            agent = ScoutAgent(mock_db)

        result = agent.parse_json_response(MOCK_SCOUT_RESPONSE)

        assert result["uf"] == "SP"
        assert result["codigo_ibge"] == "3509502"

    def test_parse_json_response_invalid(self, mock_db):
        """Testa retorno de dict vazio para JSON inválido."""
        from agents.scout import ScoutAgent

        with patch("anthropic.Anthropic"):
            agent = ScoutAgent(mock_db)

        result = agent.parse_json_response("texto sem json nenhum")
        assert result == {}

    @pytest.mark.asyncio
    async def test_execute_dry_run(self, mock_db, municipio_campinas):
        """Testa execução em dry_run — não deve chamar upsert_fonte."""
        from agents.scout import ScoutAgent

        with patch("anthropic.Anthropic"), patch.object(
            ScoutAgent, "_check_querido_diario", new=AsyncMock(return_value=None)
        ), patch.object(
            ScoutAgent, "_search_web_sources", return_value=[
                MagicMock(tipo=TipoFonte.PORTAL_TRANSPARENCIA, url="https://example.com")
            ]
        ):
            agent = ScoutAgent(mock_db)
            fontes = await agent.execute(municipio_campinas, dry_run=True)

        mock_db.upsert_fonte.assert_not_called()
        mock_db.update_municipio_status.assert_not_called()
