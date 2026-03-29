"""Testes para o Agente Cartógrafo."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.models import FonteDados, StatusMapeamento, TipoFonte, TipoSistema


MOCK_CARTOGRAPHER_RESPONSE = """{
  "url_analisada": "https://example.com/transparencia",
  "sistema_identificado": "betha",
  "versao_sistema": "2.0",
  "requer_javascript": false,
  "requer_login": false,
  "tem_captcha": false,
  "tecnologia_frontend": "angular",
  "roteiros_coleta": [
    {
      "nome": "Licitações",
      "tipo_dado": "licitacoes",
      "prioridade": "alta",
      "formato_final": "json_api",
      "passos": [
        {
          "ordem": 1,
          "acao": "GET",
          "url": "https://example.com/api/licitacoes?page={pagina}&size=20",
          "metodo": "GET",
          "descricao": "Buscar lista de licitações via API",
          "resultado_esperado": "JSON com lista de licitações paginada"
        }
      ],
      "iteracao": {
        "tipo": "paginacao",
        "parametro_pagina": "page",
        "itens_por_pagina": 20,
        "total_estimado": "~200 licitações"
      },
      "downloads": {
        "tem_arquivos": false,
        "tipos_arquivo": [],
        "url_padrao_download": null,
        "necessita_sessao": false
      }
    }
  ],
  "apis_descobertas": [],
  "observacoes": "Portal usa API REST, fácil de extrair",
  "dificuldade_estimada": "facil",
  "roteiro_validado": false
}"""


@pytest.fixture
def mock_fonte():
    return FonteDados(
        id="fonte-123",
        municipio_ibge="3509502",
        tipo=TipoFonte.PORTAL_TRANSPARENCIA,
        url="https://example.com/transparencia",
        tipo_sistema=TipoSistema.DESCONHECIDO,
        status_mapeamento=StatusMapeamento.PENDENTE,
    )


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_fonte_by_sistema = AsyncMock(return_value=None)
    db.upsert_fonte = AsyncMock()
    db.execute = AsyncMock()
    db._conn = AsyncMock()
    return db


class TestCartographerAgent:
    def test_parse_mapa_from_json(self, mock_db):
        """Testa parsing da resposta JSON do Cartógrafo."""
        from agents.cartographer import CartographerAgent

        with patch("anthropic.Anthropic"):
            agent = CartographerAgent(mock_db)

        result = agent.parse_json_response(MOCK_CARTOGRAPHER_RESPONSE)

        assert result["sistema_identificado"] == "betha"
        assert result["requer_javascript"] is False
        assert len(result["roteiros_coleta"]) == 1
        assert result["roteiros_coleta"][0]["formato_final"] == "json_api"

    def test_analyze_with_llm_returns_dict(self, mock_db):
        """Testa que _analyze_with_llm retorna dict (com mock do LLM)."""
        from agents.cartographer import CartographerAgent

        with patch("anthropic.Anthropic"):
            agent = CartographerAgent(mock_db)

        with patch.object(agent, "call_llm", return_value=MOCK_CARTOGRAPHER_RESPONSE):
            result = agent._analyze_with_llm(
                "https://example.com",
                "<html><body>Portal</body></html>",
                [],
            )

        assert isinstance(result, dict)
        assert "roteiros_coleta" in result

    @pytest.mark.asyncio
    async def test_execute_dry_run_no_db_write(self, mock_db, mock_fonte):
        """Testa que dry_run=True não escreve no banco."""
        from agents.cartographer import CartographerAgent

        with patch("anthropic.Anthropic"):
            agent = CartographerAgent(mock_db)

        with (
            patch.object(agent, "_fetch_portal", new=AsyncMock(return_value=("<html>test</html>", []))),
            patch.object(agent, "_analyze_with_llm", return_value={"roteiros_coleta": []}),
            patch.object(agent, "_validate_roteiro", new=AsyncMock(return_value=False)),
        ):
            mapa = await agent.execute(mock_fonte, dry_run=True)

        mock_db.upsert_fonte.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_reuses_cached_system_map(self, mock_db, mock_fonte):
        """Testa reutilização de mapa para sistema já mapeado."""
        from agents.cartographer import CartographerAgent

        # Simular fonte já mapeada com sistema Betha
        mock_fonte.tipo_sistema = TipoSistema.BETHA
        mock_db.get_fonte_by_sistema = AsyncMock(
            return_value={
                "mapa_navegacao": {"roteiros_coleta": [{"nome": "Licitações"}]},
                "status_mapeamento": "mapeado",
            }
        )

        with patch("anthropic.Anthropic"):
            agent = CartographerAgent(mock_db)

        mapa = await agent.execute(mock_fonte, dry_run=True)

        # Deve retornar o mapa cacheado sem chamar LLM
        assert "roteiros_coleta" in mapa
        mock_db.get_fonte_by_sistema.assert_called_once_with("betha")
