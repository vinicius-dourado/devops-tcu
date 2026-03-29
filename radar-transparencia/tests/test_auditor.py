"""Testes para o Agente Auditor."""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, patch

from core.models import Contrato, Licitacao


def _make_licitacao(**kwargs) -> Licitacao:
    defaults = {
        "municipio_ibge": "3509502",
        "fonte_id": "fonte-1",
        "objeto": "Aquisição de material de escritório",
        "confianca_extracao": 0.9,
    }
    defaults.update(kwargs)
    return Licitacao(**defaults)


def _make_contrato(**kwargs) -> Contrato:
    defaults = {
        "municipio_ibge": "3509502",
        "fonte_id": "fonte-1",
        "objeto": "Fornecimento de material",
        "confianca_extracao": 0.9,
    }
    defaults.update(kwargs)
    return Contrato(**defaults)


class TestAuditorValidation:
    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.list_licitacoes = AsyncMock(return_value=[])
        db.list_contratos = AsyncMock(return_value=[])
        db.insert_anomalia = AsyncMock()
        db.execute = AsyncMock()
        db._conn = AsyncMock()
        db._conn.commit = AsyncMock()
        return db

    def test_validate_valid_licitacao(self, mock_db):
        """Testa validação de licitação válida."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        lic = _make_licitacao(
            numero="PE 001/2024",
            valor_estimado=50000.0,
            data_publicacao=date(2024, 1, 1),
            data_abertura=date(2024, 1, 15),
        )
        result = agent._validate_licitacao(lic)
        assert result["status"] == "valido"
        assert result["problemas"] == []

    def test_validate_invalid_cnpj(self, mock_db):
        """Testa detecção de CNPJ inválido."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        lic = _make_licitacao(vencedor_cnpj="12345678000100")  # CNPJ inválido
        result = agent._validate_licitacao(lic)
        assert result["status"] == "invalido"
        assert any("CNPJ" in p for p in result["problemas"])

    def test_validate_valid_cnpj(self, mock_db):
        """Testa que CNPJ válido não gera problema."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        lic = _make_licitacao(vencedor_cnpj="00000000000191")  # Banco do Brasil — válido
        result = agent._validate_licitacao(lic)
        cnpj_problems = [p for p in result["problemas"] if "CNPJ" in p]
        assert cnpj_problems == []

    def test_validate_negative_value(self, mock_db):
        """Testa detecção de valor negativo."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        lic = _make_licitacao(valor_estimado=-1000.0)
        result = agent._validate_licitacao(lic)
        assert result["status"] == "invalido"

    def test_validate_inconsistent_dates(self, mock_db):
        """Testa detecção de datas inconsistentes."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        lic = _make_licitacao(
            data_publicacao=date(2024, 2, 1),
            data_abertura=date(2024, 1, 1),  # abertura antes da publicação
        )
        result = agent._validate_licitacao(lic)
        assert result["status"] in ("invalido", "alerta")

    def test_detect_empresa_frequente(self, mock_db):
        """Testa detecção de empresa vencendo muitas licitações."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        # 8 licitações com a mesma empresa vencedora (80%)
        licitacoes = [
            _make_licitacao(vencedor_nome="Empresa XYZ LTDA", numero=f"PE {i:03d}/2024")
            for i in range(8)
        ] + [
            _make_licitacao(vencedor_nome="Outra Empresa SA", numero=f"PE {i:03d}/2024")
            for i in range(8, 10)
        ]

        anomalias = agent._detect_anomalias("3509502", licitacoes, [])
        tipos = [a.tipo for a in anomalias]
        assert "EMPRESA_FREQUENTE" in tipos

    def test_detect_dispensa_valor_limite(self, mock_db):
        """Testa detecção de dispensa próxima ao teto legal."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        licitacoes = [
            _make_licitacao(
                modalidade="dispensa de licitação",
                valor_estimado=48000.0,  # 96% do teto de R$ 50.000
            )
        ]
        anomalias = agent._detect_anomalias("3509502", licitacoes, [])
        tipos = [a.tipo for a in anomalias]
        assert "DISPENSA_VALOR_LIMITE" in tipos

    def test_detect_cnpj_invalido(self, mock_db):
        """Testa detecção de CNPJs inválidos."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        licitacoes = [_make_licitacao(vencedor_cnpj="12345678000100")]
        anomalias = agent._detect_anomalias("3509502", licitacoes, [])
        tipos = [a.tipo for a in anomalias]
        assert "CNPJ_INVALIDO" in tipos

    def test_quality_score_empty(self, mock_db):
        """Testa score de qualidade com dados vazios."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        score = agent._compute_quality_score([], [], {"total_itens": 0, "validos": 0, "invalidos": 0})
        assert score == 0.0

    def test_quality_score_all_valid(self, mock_db):
        """Testa score de qualidade com todos os itens válidos."""
        from agents.auditor import AuditorAgent

        with patch("anthropic.Anthropic"):
            agent = AuditorAgent(mock_db)

        lics = [
            _make_licitacao(
                numero="PE 001/2024",
                modalidade="pregão eletrônico",
                valor_estimado=50000.0,
                data_abertura=date(2024, 3, 15),
                vencedor_nome="Empresa Boa",
            )
        ]
        score = agent._compute_quality_score(
            lics, [], {"total_itens": 1, "validos": 1, "invalidos": 0, "alertas": 0}
        )
        assert score > 0.5
