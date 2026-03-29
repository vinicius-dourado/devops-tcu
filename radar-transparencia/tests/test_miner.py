"""Testes para o Agente Minerador e Extractors."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.models import FonteDados, StatusMapeamento, TipoFonte, TipoSistema


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.insert_licitacao = AsyncMock()
    db.insert_contrato = AsyncMock()
    db.insert_publicacao = AsyncMock()
    db.upsert_fonte = AsyncMock()
    return db


@pytest.fixture
def mock_fonte():
    return FonteDados(
        id="fonte-miner-1",
        municipio_ibge="3509502",
        tipo=TipoFonte.PORTAL_TRANSPARENCIA,
        url="https://example.com/licitacoes",
        tipo_sistema=TipoSistema.BETHA,
        status_mapeamento=StatusMapeamento.MAPEADO,
        mapa_navegacao={
            "roteiros_coleta": [
                {
                    "nome": "Licitações",
                    "tipo_dado": "licitacoes",
                    "formato_final": "json_api",
                    "passos": [
                        {
                            "ordem": 1,
                            "acao": "GET",
                            "url": "https://example.com/api/licitacoes",
                            "metodo": "GET",
                        }
                    ],
                    "iteracao": {
                        "tipo": "paginacao",
                        "parametro_pagina": "page",
                        "itens_por_pagina": 10,
                    },
                }
            ]
        },
    )


class TestMinerAgent:
    def test_parse_float_values(self, mock_db):
        """Testa conversão de valores monetários."""
        from agents.miner import _parse_float

        assert _parse_float(50000.0) == 50000.0
        assert _parse_float("50000.00") == 50000.0
        assert _parse_float("R$ 150.000,00") == 150000.0
        assert _parse_float(None) is None
        assert _parse_float("não é número") is None

    def test_parse_date_formats(self, mock_db):
        """Testa parsing de diferentes formatos de data."""
        from agents.miner import _parse_date
        from datetime import date

        assert _parse_date("2024-03-15") == date(2024, 3, 15)
        assert _parse_date("15/03/2024") == date(2024, 3, 15)
        assert _parse_date("15-03-2024") == date(2024, 3, 15)
        assert _parse_date(None) is None
        assert _parse_date("data-invalida") is None
        assert _parse_date("") is None

    def test_to_licitacao_mapping(self, mock_db, mock_fonte):
        """Testa conversão de dict para objeto Licitacao."""
        from agents.miner import MinerAgent
        from datetime import date

        with patch("anthropic.Anthropic"):
            agent = MinerAgent(mock_db)

        item = {
            "numero": "PE 001/2024",
            "modalidade": "pregão eletrônico",
            "objeto": "Aquisição de material de escritório",
            "valor_estimado": 50000.0,
            "data_abertura": "2024-03-15",
            "situacao": "encerrada",
            "vencedor_nome": "Papelaria XYZ",
            "vencedor_cnpj": "12345678000199",
            "confianca": 0.95,
        }
        lic = agent._to_licitacao(item, mock_fonte, 0.85)

        assert lic.numero == "PE 001/2024"
        assert lic.modalidade == "pregão eletrônico"
        assert lic.valor_estimado == 50000.0
        assert lic.data_abertura == date(2024, 3, 15)
        assert lic.confianca_extracao == 0.95

    def test_to_contrato_mapping(self, mock_db, mock_fonte):
        """Testa conversão de dict para objeto Contrato."""
        from agents.miner import MinerAgent

        with patch("anthropic.Anthropic"):
            agent = MinerAgent(mock_db)

        item = {
            "numero": "CT 010/2024",
            "objeto": "Fornecimento de material",
            "contratado_nome": "Papelaria XYZ",
            "contratado_cnpj": "12345678000199",
            "valor": "R$ 48.500,00",
            "confianca": 0.90,
        }
        ct = agent._to_contrato(item, mock_fonte, 0.85)

        assert ct.numero == "CT 010/2024"
        assert ct.valor == 48500.0
        assert ct.confianca_extracao == 0.90

    def test_extract_from_csv(self, mock_db):
        """Testa extração de CSV com mock do LLM extractor."""
        from agents.miner import MinerAgent

        with patch("anthropic.Anthropic"):
            agent = MinerAgent(mock_db)

        csv_text = """numero,objeto,valor_estimado
PE 001/2024,Aquisição material,50000
PE 002/2024,Serviços de TI,120000
"""
        with patch.object(
            agent.llm_extractor,
            "extract_procurement_data",
            return_value={
                "licitacoes": [
                    {"numero": "PE 001/2024", "objeto": "Aquisição material"},
                    {"numero": "PE 002/2024", "objeto": "Serviços de TI"},
                ],
                "contratos": [],
                "outros_atos": [],
                "total_itens_extraidos": 2,
            },
        ):
            result = agent._extract_from_csv(csv_text)

        assert len(result.get("licitacoes", [])) == 2

    def test_build_url_pagination(self, mock_db):
        """Testa construção de URL paginada."""
        from agents.miner import MinerAgent

        with patch("anthropic.Anthropic"):
            agent = MinerAgent(mock_db)

        url = agent._build_url(
            "https://example.com/api?size=20",
            "page",
            2,
            20,
            "paginacao",
        )
        assert "page=3" in url  # Page 2 (0-indexed) → page=3 (1-indexed)

    @pytest.mark.asyncio
    async def test_execute_no_map(self, mock_db):
        """Testa que execução sem mapa de navegação retorna listas vazias."""
        from agents.miner import MinerAgent

        fonte_sem_mapa = FonteDados(
            id="f1",
            municipio_ibge="3509502",
            tipo=TipoFonte.PORTAL_TRANSPARENCIA,
            url="https://example.com",
            mapa_navegacao=None,
        )

        with patch("anthropic.Anthropic"):
            agent = MinerAgent(mock_db)

        lics, cts, pubs = await agent.execute(fonte_sem_mapa)
        assert lics == []
        assert cts == []
        assert pubs == []


class TestHTMLExtractor:
    def test_extract_table(self):
        """Testa extração de tabela HTML simples."""
        from extractors.html_extractor import HTMLExtractor

        html = """
        <table>
          <tr><th>Número</th><th>Objeto</th><th>Valor</th></tr>
          <tr><td>PE 001/2024</td><td>Material escritório</td><td>50000</td></tr>
          <tr><td>PE 002/2024</td><td>Serviços TI</td><td>120000</td></tr>
        </table>
        """
        extractor = HTMLExtractor()
        rows = extractor.extract_table(html)

        assert len(rows) == 2
        assert rows[0]["Número"] == "PE 001/2024"
        assert rows[0]["Objeto"] == "Material escritório"

    def test_extract_links(self):
        """Testa extração de links."""
        from extractors.html_extractor import HTMLExtractor

        html = """
        <div>
          <a href="https://example.com/lic/1">Licitação 1</a>
          <a href="https://example.com/lic/2">Licitação 2</a>
          <a href="">Link vazio</a>
        </div>
        """
        extractor = HTMLExtractor()
        links = extractor.extract_links(html, "a")

        assert len(links) == 2
        assert "https://example.com/lic/1" in links

    def test_extract_text(self):
        """Testa extração de texto."""
        from extractors.html_extractor import HTMLExtractor

        html = "<div><p>PREGÃO ELETRÔNICO Nº 001/2024</p><p>Objeto: Material</p></div>"
        extractor = HTMLExtractor()
        text = extractor.extract_text(html, "div")

        assert "PREGÃO ELETRÔNICO" in text
        assert "Material" in text

    def test_extract_table_no_data(self):
        """Testa extração em HTML sem tabela."""
        from extractors.html_extractor import HTMLExtractor

        extractor = HTMLExtractor()
        rows = extractor.extract_table("<div>sem tabela</div>")
        assert rows == []

    def test_resolve_url(self):
        """Testa resolução de URLs relativas."""
        from extractors.html_extractor import HTMLExtractor

        extractor = HTMLExtractor()
        assert extractor.resolve_url("/licitacoes/1", "https://example.com") == "https://example.com/licitacoes/1"
        assert extractor.resolve_url("https://other.com/x", "https://example.com") == "https://other.com/x"
