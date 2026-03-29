"""Extrator adaptativo de dados via LLM para conteúdo não estruturado."""

from __future__ import annotations

from typing import Any

import anthropic

from agents.prompts.miner_prompts import MINER_SYSTEM_PROMPT
from core.logger import get_logger

logger = get_logger("LLMExtractor")

# Limite de caracteres enviados ao LLM por chamada
_MAX_TEXT_LENGTH = 12000


class LLMExtractor:
    """Extrai dados de licitações/contratos de texto livre usando o LLM.

    Usado para PDFs de diários oficiais, HTML não estruturado e texto corrido.
    """

    def __init__(self, client: anthropic.Anthropic, model: str = "claude-sonnet-4-20250514") -> None:
        """Inicializa o extrator.

        Args:
            client: Cliente Anthropic já instanciado.
            model: Modelo a usar para extração.
        """
        self.client = client
        self.model = model

    def extract_procurement_data(
        self,
        text: str,
        context: str | None = None,
        municipio: str | None = None,
    ) -> dict[str, Any]:
        """Extrai licitações, contratos e outros atos de texto livre.

        Args:
            text: Texto bruto (PDF, HTML, texto corrido).
            context: Contexto adicional (ex: nome do portal, data).
            municipio: Nome do município para contextualizar extração.

        Returns:
            Dicionário com keys: licitacoes, contratos, outros_atos, total_itens_extraidos.
        """
        if not text or not text.strip():
            return {"licitacoes": [], "contratos": [], "outros_atos": [], "total_itens_extraidos": 0}

        # Truncar texto se muito longo
        text_truncated = text[:_MAX_TEXT_LENGTH]
        if len(text) > _MAX_TEXT_LENGTH:
            logger.warning(
                f"[LLMExtractor] Texto truncado de {len(text)} para {_MAX_TEXT_LENGTH} chars"
            )

        context_prefix = ""
        if municipio:
            context_prefix += f"Município: {municipio}\n"
        if context:
            context_prefix += f"Contexto: {context}\n"
        if context_prefix:
            context_prefix += "\n"

        user_message = f"{context_prefix}Texto para extração:\n\n{text_truncated}"

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8096,
                system=MINER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            raw_text = response.content[0].text
            result = self._parse_response(raw_text)
            logger.debug(
                f"[LLMExtractor] Extraídos: {len(result.get('licitacoes', []))} licitações, "
                f"{len(result.get('contratos', []))} contratos"
            )
            return result

        except Exception as e:
            logger.error(f"[LLMExtractor] Erro na extração LLM: {e}")
            return {"licitacoes": [], "contratos": [], "outros_atos": [], "total_itens_extraidos": 0}

    def extract_procurement_data_chunked(
        self,
        text: str,
        chunk_size: int = _MAX_TEXT_LENGTH,
        overlap: int = 500,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Extrai dados de textos longos dividindo em chunks com sobreposição.

        Args:
            text: Texto longo (ex: PDF de diário oficial completo).
            chunk_size: Tamanho de cada chunk em caracteres.
            overlap: Sobreposição entre chunks para evitar cortes de atos.
            **kwargs: Passados para extract_procurement_data.

        Returns:
            Resultado consolidado com todos os itens extraídos.
        """
        if len(text) <= chunk_size:
            return self.extract_procurement_data(text, **kwargs)

        # Dividir em chunks com sobreposição
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(text[start:end])
            start = end - overlap
            if start >= len(text):
                break

        logger.info(f"[LLMExtractor] Processando {len(chunks)} chunks...")

        all_licitacoes: list[dict[str, Any]] = []
        all_contratos: list[dict[str, Any]] = []
        all_outros: list[dict[str, Any]] = []

        seen_numeros: set[str] = set()  # Deduplicação básica por número

        for i, chunk in enumerate(chunks):
            result = self.extract_procurement_data(
                chunk,
                context=f"Chunk {i + 1}/{len(chunks)}",
                **kwargs,
            )
            # Deduplicar por número de licitação/contrato
            for lic in result.get("licitacoes", []):
                num = lic.get("numero", "")
                key = f"lic_{num}"
                if num and key in seen_numeros:
                    continue
                if num:
                    seen_numeros.add(key)
                all_licitacoes.append(lic)

            for ct in result.get("contratos", []):
                num = ct.get("numero", "")
                key = f"ct_{num}"
                if num and key in seen_numeros:
                    continue
                if num:
                    seen_numeros.add(key)
                all_contratos.append(ct)

            all_outros.extend(result.get("outros_atos", []))

        total = len(all_licitacoes) + len(all_contratos) + len(all_outros)
        return {
            "licitacoes": all_licitacoes,
            "contratos": all_contratos,
            "outros_atos": all_outros,
            "total_itens_extraidos": total,
        }

    def _parse_response(self, text: str) -> dict[str, Any]:
        """Parseia a resposta JSON do LLM."""
        text = text.strip()

        # Remove blocos de código markdown
        for prefix in ("```json\n", "```json", "```\n", "```"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        import json
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                try:
                    data = json.loads(text[start:end])
                except json.JSONDecodeError:
                    return {"licitacoes": [], "contratos": [], "outros_atos": [], "total_itens_extraidos": 0}
            else:
                return {"licitacoes": [], "contratos": [], "outros_atos": [], "total_itens_extraidos": 0}

        licitacoes = data.get("licitacoes", [])
        contratos = data.get("contratos", [])
        outros = data.get("outros_atos", [])
        total = len(licitacoes) + len(contratos) + len(outros)

        return {
            "licitacoes": licitacoes if isinstance(licitacoes, list) else [],
            "contratos": contratos if isinstance(contratos, list) else [],
            "outros_atos": outros if isinstance(outros, list) else [],
            "total_itens_extraidos": data.get("total_itens_extraidos", total),
            "observacoes": data.get("observacoes", ""),
        }
