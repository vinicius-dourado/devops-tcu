"""Agente Auditor — Validação, normalização e detecção de anomalias."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections import Counter
from datetime import date, datetime
from typing import Any

from agents.base import BaseAgent
from agents.prompts.auditor_prompts import AUDITOR_SYSTEM_PROMPT
from config.settings import settings
from core.database import Database
from core.models import Anomalia, Contrato, Licitacao
from integrations.cnpj import clean_cnpj, validate_cnpj


# Tetos de dispensa de licitação (Lei 14.133/2021)
TETO_DISPENSA_OBRAS = 100_000.0
TETO_DISPENSA_SERVICOS = 50_000.0
LIMITE_ALERTA_VALOR = 10_000_000.0


class AuditorAgent(BaseAgent):
    """Agente de auditoria dos dados coletados pelo Minerador.

    Para cada município:
    1. Valida CNPJs, valores e datas
    2. Normaliza nomes, modalidades e valores
    3. Detecta anomalias (empresa frequente, fracionamento, etc.)
    4. Calcula score de qualidade dos dados
    5. Salva anomalias no banco
    """

    def __init__(self, db: Database, model: str | None = None) -> None:
        super().__init__(db, model or settings.LLM_MODEL)

    async def execute(  # type: ignore[override]
        self,
        municipio_ibge: str,
        licitacoes: list[Licitacao] | None = None,
        contratos: list[Contrato] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Audita os dados de um município.

        Args:
            municipio_ibge: Código IBGE do município.
            licitacoes: Lista de licitações a auditar. Se None, busca do banco.
            contratos: Lista de contratos a auditar. Se None, busca do banco.
            dry_run: Se True, não persiste anomalias no banco.

        Returns:
            Relatório de auditoria com validações, anomalias e score de qualidade.
        """
        self.stats["processados"] += 1
        self.logger.info(f"[Auditor] Auditando município {municipio_ibge}")

        # Buscar dados do banco se não fornecidos
        if licitacoes is None:
            rows = await self.db.list_licitacoes(municipio_ibge=municipio_ibge, limit=500)
            licitacoes = [Licitacao(**row) for row in rows]
        if contratos is None:
            rows = await self.db.list_contratos(municipio_ibge=municipio_ibge, limit=500)
            contratos = [Contrato(**row) for row in rows]

        # 1. Validações individuais
        validacoes = self._validate_all(licitacoes, contratos)

        # 2. Normalização
        licitacoes = self._normalize_licitacoes(licitacoes)
        contratos = self._normalize_contratos(contratos)

        # 3. Detecção de anomalias
        anomalias = self._detect_anomalias(municipio_ibge, licitacoes, contratos)

        # 4. Auditoria LLM (para anomalias mais complexas)
        llm_anomalias = await asyncio.to_thread(
            self._llm_audit, municipio_ibge, licitacoes[:50], contratos[:50]
        )
        anomalias.extend(llm_anomalias)

        # 5. Score de qualidade
        score = self._compute_quality_score(licitacoes, contratos, validacoes)

        # 6. Persistir anomalias
        if not dry_run:
            for anomalia in anomalias:
                anomalia_dict = anomalia.model_dump(mode="json")
                await self.db.insert_anomalia(anomalia_dict)

            # Marcar como validado no banco
            for lic in licitacoes:
                if lic.id:
                    await self.db.execute(
                        "UPDATE licitacoes SET validado = 1 WHERE id = ?", (lic.id,)
                    )
                    await self.db._conn.commit()  # type: ignore[union-attr]

        self.stats["sucesso"] += 1
        self.logger.info(
            f"[Auditor] Município {municipio_ibge}: "
            f"score={score:.2f}, anomalias={len(anomalias)}"
        )

        return {
            "municipio_ibge": municipio_ibge,
            "validacoes": validacoes,
            "anomalias": [a.model_dump(mode="json") for a in anomalias],
            "score_qualidade_dados": score,
            "total_licitacoes": len(licitacoes),
            "total_contratos": len(contratos),
        }

    def _validate_all(
        self,
        licitacoes: list[Licitacao],
        contratos: list[Contrato],
    ) -> dict[str, Any]:
        """Valida todos os itens e retorna relatório de validação."""
        detalhes: list[dict[str, Any]] = []
        validos = invalidos = alertas = 0

        for lic in licitacoes:
            resultado = self._validate_licitacao(lic)
            detalhes.append(resultado)
            if resultado["status"] == "valido":
                validos += 1
            elif resultado["status"] == "invalido":
                invalidos += 1
            else:
                alertas += 1

        for ct in contratos:
            resultado = self._validate_contrato(ct)
            detalhes.append(resultado)
            if resultado["status"] == "valido":
                validos += 1
            elif resultado["status"] == "invalido":
                invalidos += 1
            else:
                alertas += 1

        return {
            "total_itens": len(licitacoes) + len(contratos),
            "validos": validos,
            "invalidos": invalidos,
            "alertas": alertas,
            "detalhes": detalhes[:100],  # Limitar detalhes no relatório
        }

    def _validate_licitacao(self, lic: Licitacao) -> dict[str, Any]:
        """Valida uma licitação individualmente."""
        problemas: list[str] = []
        correcoes: list[str] = []
        status = "valido"

        # Validar CNPJ do vencedor
        if lic.vencedor_cnpj:
            cnpj_clean = clean_cnpj(lic.vencedor_cnpj)
            if not validate_cnpj(cnpj_clean):
                problemas.append("CNPJ do vencedor inválido")
                status = "invalido"

        # Validar valor
        if lic.valor_estimado is not None:
            if lic.valor_estimado < 0:
                problemas.append("Valor estimado negativo")
                status = "invalido"
            elif lic.valor_estimado > LIMITE_ALERTA_VALOR:
                problemas.append(f"Valor estimado acima de R$ {LIMITE_ALERTA_VALOR:,.0f}")
                status = "alerta"

        # Validar datas
        if lic.data_publicacao and lic.data_abertura:
            if lic.data_publicacao > lic.data_abertura:
                problemas.append("Data de publicação posterior à data de abertura")
                status = "alerta"

        # Objeto vago
        if lic.objeto and len(lic.objeto.strip()) < 10:
            problemas.append("Objeto muito vago ou curto")
            status = "alerta"

        return {
            "item_id": lic.id,
            "tipo": "licitacao",
            "numero": lic.numero,
            "status": status,
            "problemas": problemas,
            "correcoes_aplicadas": correcoes,
        }

    def _validate_contrato(self, ct: Contrato) -> dict[str, Any]:
        """Valida um contrato individualmente."""
        problemas: list[str] = []
        correcoes: list[str] = []
        status = "valido"

        # Validar CNPJ
        if ct.contratado_cnpj:
            cnpj_clean = clean_cnpj(ct.contratado_cnpj)
            if not validate_cnpj(cnpj_clean):
                problemas.append("CNPJ do contratado inválido")
                status = "invalido"

        # Validar valor
        if ct.valor is not None:
            if ct.valor < 0:
                problemas.append("Valor do contrato negativo")
                status = "invalido"
            elif ct.valor > LIMITE_ALERTA_VALOR:
                problemas.append(f"Valor do contrato acima de R$ {LIMITE_ALERTA_VALOR:,.0f}")
                status = "alerta"

        # Validar datas
        if ct.data_inicio and ct.data_fim:
            if ct.data_inicio > ct.data_fim:
                problemas.append("Data de início posterior à data de fim")
                status = "invalido"

        return {
            "item_id": ct.id,
            "tipo": "contrato",
            "numero": ct.numero,
            "status": status,
            "problemas": problemas,
            "correcoes_aplicadas": correcoes,
        }

    def _normalize_licitacoes(self, licitacoes: list[Licitacao]) -> list[Licitacao]:
        """Normaliza modalidades e outros campos de licitações."""
        modalidade_map = {
            "pe ": "pregão eletrônico",
            "pp ": "pregão presencial",
            "cc ": "concorrência",
            "tp ": "tomada de preços",
            "cv ": "convite",
            "dl ": "dispensa de licitação",
            "il ": "inexigibilidade de licitação",
        }
        normalized = []
        for lic in licitacoes:
            if lic.modalidade:
                modal_lower = lic.modalidade.lower()
                for prefix, value in modalidade_map.items():
                    if modal_lower.startswith(prefix) or prefix.strip() in modal_lower:
                        lic = lic.model_copy(update={"modalidade": value})
                        break
            # Normalizar CNPJ
            if lic.vencedor_cnpj:
                lic = lic.model_copy(update={"vencedor_cnpj": clean_cnpj(lic.vencedor_cnpj)})
            normalized.append(lic)
        return normalized

    def _normalize_contratos(self, contratos: list[Contrato]) -> list[Contrato]:
        """Normaliza nomes e CNPJs de contratos."""
        normalized = []
        for ct in contratos:
            updates: dict[str, Any] = {}
            if ct.contratado_cnpj:
                updates["contratado_cnpj"] = clean_cnpj(ct.contratado_cnpj)
            if ct.contratado_nome:
                # Normalizar razão social: maiúsculas, remover espaços duplos
                updates["contratado_nome"] = re.sub(
                    r"\s+", " ", ct.contratado_nome.upper().strip()
                )
            if updates:
                ct = ct.model_copy(update=updates)
            normalized.append(ct)
        return normalized

    def _detect_anomalias(
        self,
        municipio_ibge: str,
        licitacoes: list[Licitacao],
        contratos: list[Contrato],
    ) -> list[Anomalia]:
        """Detecta anomalias nos dados usando regras determinísticas."""
        anomalias: list[Anomalia] = []

        # EMPRESA_FREQUENTE: empresa vencendo >50% das licitações
        if licitacoes:
            vencedores = [
                lic.vencedor_nome.upper().strip()
                for lic in licitacoes
                if lic.vencedor_nome
            ]
            if vencedores:
                contagem = Counter(vencedores)
                total = len(licitacoes)
                for empresa, count in contagem.most_common(5):
                    if count >= 5 and count / total > 0.4:
                        anomalias.append(
                            Anomalia(
                                id=str(uuid.uuid4()),
                                municipio_ibge=municipio_ibge,
                                tipo="EMPRESA_FREQUENTE",
                                severidade="alta" if count / total > 0.6 else "media",
                                descricao=f"Empresa '{empresa}' venceu {count} de {total} licitações ({count/total:.0%})",
                                dados_referencia={"empresa": empresa, "total_vitorias": count, "total_licitacoes": total},
                            )
                        )

        # DISPENSA_VALOR_LIMITE: dispensa próxima ao teto
        for lic in licitacoes:
            if lic.modalidade and "dispensa" in lic.modalidade.lower():
                valor = lic.valor_estimado or lic.valor_contratado
                if valor:
                    for teto, label in [
                        (TETO_DISPENSA_SERVICOS, "serviços"),
                        (TETO_DISPENSA_OBRAS, "obras"),
                    ]:
                        if valor >= teto * 0.85 and valor <= teto * 1.05:
                            anomalias.append(
                                Anomalia(
                                    id=str(uuid.uuid4()),
                                    municipio_ibge=municipio_ibge,
                                    tipo="DISPENSA_VALOR_LIMITE",
                                    severidade="media",
                                    descricao=(
                                        f"Dispensa {lic.numero} com valor R$ {valor:,.2f} "
                                        f"próximo ao teto de {label} (R$ {teto:,.2f})"
                                    ),
                                    dados_referencia={
                                        "licitacao": lic.numero,
                                        "valor": valor,
                                        "teto": teto,
                                        "tipo": label,
                                    },
                                )
                            )

        # OBJETO_GENERICO: objetos muito vagos
        objetos_vagos = [
            "serviços diversos", "material de consumo", "aquisição diversa",
            "serviços gerais", "despesas diversas",
        ]
        for lic in licitacoes:
            if lic.objeto:
                obj_lower = lic.objeto.lower()
                for vago in objetos_vagos:
                    if vago in obj_lower and len(lic.objeto) < 50:
                        anomalias.append(
                            Anomalia(
                                id=str(uuid.uuid4()),
                                municipio_ibge=municipio_ibge,
                                tipo="OBJETO_GENERICO",
                                severidade="baixa",
                                descricao=f"Licitação {lic.numero} com objeto genérico: '{lic.objeto}'",
                                dados_referencia={"licitacao": lic.numero, "objeto": lic.objeto},
                            )
                        )
                        break

        # FRACIONAMENTO_SUSPEITO: múltiplas dispensas similares em período próximo
        dispensas = [
            lic for lic in licitacoes
            if lic.modalidade and "dispensa" in lic.modalidade.lower()
            and lic.valor_estimado
        ]
        if len(dispensas) >= 3:
            total_valor_dispensas = sum(
                lic.valor_estimado for lic in dispensas if lic.valor_estimado
            )
            if total_valor_dispensas > TETO_DISPENSA_SERVICOS * 3:
                anomalias.append(
                    Anomalia(
                        id=str(uuid.uuid4()),
                        municipio_ibge=municipio_ibge,
                        tipo="FRACIONAMENTO_SUSPEITO",
                        severidade="alta",
                        descricao=(
                            f"Município possui {len(dispensas)} dispensas de licitação "
                            f"totalizando R$ {total_valor_dispensas:,.2f}"
                        ),
                        dados_referencia={
                            "total_dispensas": len(dispensas),
                            "valor_total": total_valor_dispensas,
                        },
                    )
                )

        # CNPJ_INVALIDO: CNPJs inválidos detectados
        cnpjs_invalidos: list[str] = []
        for lic in licitacoes:
            if lic.vencedor_cnpj:
                cnpj = clean_cnpj(lic.vencedor_cnpj)
                if cnpj and not validate_cnpj(cnpj):
                    cnpjs_invalidos.append(cnpj)
        for ct in contratos:
            if ct.contratado_cnpj:
                cnpj = clean_cnpj(ct.contratado_cnpj)
                if cnpj and not validate_cnpj(cnpj):
                    cnpjs_invalidos.append(cnpj)

        if cnpjs_invalidos:
            anomalias.append(
                Anomalia(
                    id=str(uuid.uuid4()),
                    municipio_ibge=municipio_ibge,
                    tipo="CNPJ_INVALIDO",
                    severidade="media",
                    descricao=f"{len(cnpjs_invalidos)} CNPJ(s) inválido(s) encontrado(s)",
                    dados_referencia={"cnpjs": list(set(cnpjs_invalidos))[:10]},
                )
            )

        return anomalias

    def _llm_audit(
        self,
        municipio_ibge: str,
        licitacoes: list[Licitacao],
        contratos: list[Contrato],
    ) -> list[Anomalia]:
        """Usa o LLM para detectar anomalias mais complexas."""
        if not licitacoes and not contratos:
            return []

        import json as _json

        data = {
            "municipio_ibge": municipio_ibge,
            "licitacoes": [
                {
                    "numero": lic.numero,
                    "modalidade": lic.modalidade,
                    "objeto": lic.objeto,
                    "valor_estimado": lic.valor_estimado,
                    "data_abertura": str(lic.data_abertura) if lic.data_abertura else None,
                    "vencedor_nome": lic.vencedor_nome,
                    "vencedor_cnpj": lic.vencedor_cnpj,
                    "situacao": lic.situacao,
                }
                for lic in licitacoes
            ],
            "contratos": [
                {
                    "numero": ct.numero,
                    "objeto": ct.objeto,
                    "contratado_nome": ct.contratado_nome,
                    "contratado_cnpj": ct.contratado_cnpj,
                    "valor": ct.valor,
                    "data_assinatura": str(ct.data_assinatura) if ct.data_assinatura else None,
                }
                for ct in contratos
            ],
        }

        try:
            text = self.call_llm(
                AUDITOR_SYSTEM_PROMPT,
                f"Audite os seguintes dados de transparência:\n{_json.dumps(data, ensure_ascii=False)}",
                max_tokens=4096,
            )
            result = self.parse_json_response(text)
            anomalias: list[Anomalia] = []
            for item in result.get("anomalias", []):
                anomalias.append(
                    Anomalia(
                        id=str(uuid.uuid4()),
                        municipio_ibge=municipio_ibge,
                        tipo=item.get("tipo", "OUTRO"),
                        descricao=item.get("descricao", ""),
                        severidade=item.get("severidade", "baixa"),
                        dados_referencia=item.get("dados", {}),
                    )
                )
            return anomalias
        except Exception as e:
            self.logger.error(f"[Auditor] Erro na auditoria LLM: {e}")
            return []

    def _compute_quality_score(
        self,
        licitacoes: list[Licitacao],
        contratos: list[Contrato],
        validacoes: dict[str, Any],
    ) -> float:
        """Calcula o score de qualidade dos dados (0 a 1)."""
        total = validacoes.get("total_itens", 0)
        if total == 0:
            return 0.0

        validos = validacoes.get("validos", 0)
        invalidos = validacoes.get("invalidos", 0)

        # Base: proporção de itens válidos
        base_score = validos / total if total > 0 else 0.0

        # Penalizar por inválidos
        penalty = (invalidos / total) * 0.5

        # Bônus por completude dos campos obrigatórios
        completeness_scores: list[float] = []
        for lic in licitacoes:
            filled = sum([
                bool(lic.numero), bool(lic.modalidade), bool(lic.objeto),
                lic.valor_estimado is not None, bool(lic.data_abertura),
            ])
            completeness_scores.append(filled / 5.0)
        for ct in contratos:
            filled = sum([
                bool(ct.numero), bool(ct.objeto),
                ct.valor is not None, bool(ct.data_assinatura),
                bool(ct.contratado_nome),
            ])
            completeness_scores.append(filled / 5.0)

        avg_completeness = (
            sum(completeness_scores) / len(completeness_scores)
            if completeness_scores else 0.5
        )

        score = (base_score * 0.5 + avg_completeness * 0.5) - penalty
        return max(0.0, min(1.0, score))
