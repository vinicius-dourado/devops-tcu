"""Agente Minerador — Extração de dados seguindo roteiros do Cartógrafo."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.base import BaseAgent
from config.settings import settings
from core.database import Database
from core.models import Contrato, FonteDados, Licitacao, PublicacaoDiario, TipoFonte
from extractors.html_extractor import HTMLExtractor
from extractors.llm_extractor import LLMExtractor
from extractors.pdf_extractor import PDFExtractor

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Mapeamento de confiança por formato
CONFIDENCE_MAP = {
    "json_api": 0.95,
    "csv_download": 0.90,
    "xls_download": 0.88,
    "html_tabela": 0.85,
    "pdf_download": 0.75,
    "texto_corrido": 0.65,
    "javascript_rendered": 0.70,
}


class MinerAgent(BaseAgent):
    """Agente de extração de dados que segue os roteiros do Cartógrafo.

    Para cada fonte mapeada:
    1. Lê o roteiro de coleta do mapa de navegação
    2. Executa cada passo do roteiro (requisições HTTP, downloads, parsing)
    3. Itera por todas as páginas/datas conforme estratégia definida
    4. Extrai dados estruturados (Licitacao, Contrato, PublicacaoDiario)
    5. Salva os dados no banco com score de confiança
    """

    def __init__(self, db: Database, model: str | None = None) -> None:
        super().__init__(db, model or settings.LLM_MODEL)
        self.pdf_extractor = PDFExtractor()
        self.html_extractor = HTMLExtractor()
        self.llm_extractor = LLMExtractor(client=self.client, model=self.model)

    async def execute(  # type: ignore[override]
        self,
        fonte: FonteDados,
        dry_run: bool = False,
        max_pages: int = 50,
    ) -> tuple[list[Licitacao], list[Contrato], list[PublicacaoDiario]]:
        """Extrai dados de uma fonte seguindo o roteiro do Cartógrafo.

        Args:
            fonte: Fonte com mapa_navegacao preenchido.
            dry_run: Se True, não persiste no banco.
            max_pages: Limite de páginas por roteiro para evitar loops infinitos.

        Returns:
            Tuple (licitacoes, contratos, publicacoes).
        """
        self.stats["processados"] += 1

        if not fonte.mapa_navegacao:
            self.logger.warning(f"[Minerador] Fonte {fonte.url} sem mapa de navegação")
            self.stats["ignorados"] += 1
            return [], [], []

        all_licitacoes: list[Licitacao] = []
        all_contratos: list[Contrato] = []
        all_publicacoes: list[PublicacaoDiario] = []

        # Caso especial: Querido Diário usa API própria
        if fonte.tipo == TipoFonte.QUERIDO_DIARIO:
            lics, cts, pubs = await self._mine_querido_diario(fonte, dry_run)
            return lics, cts, pubs

        mapa = fonte.mapa_navegacao
        roteiros = mapa.get("roteiros_coleta", [])

        if not roteiros:
            self.logger.warning(f"[Minerador] Nenhum roteiro em {fonte.url}")
            self.stats["ignorados"] += 1
            return [], [], []

        for roteiro in roteiros:
            try:
                lics, cts, pubs = await self._execute_roteiro(
                    roteiro, fonte, dry_run, max_pages
                )
                all_licitacoes.extend(lics)
                all_contratos.extend(cts)
                all_publicacoes.extend(pubs)
            except Exception as e:
                self.logger.error(f"[Minerador] Erro no roteiro '{roteiro.get('nome')}': {e}")
                self.stats["erro"] += 1

        self.stats["sucesso"] += 1
        self.logger.info(
            f"[Minerador] {fonte.url}: "
            f"{len(all_licitacoes)} licitações, "
            f"{len(all_contratos)} contratos, "
            f"{len(all_publicacoes)} publicações"
        )

        # Atualizar última coleta
        if not dry_run:
            fonte_dict: dict[str, Any] = {
                "id": fonte.id,
                "municipio_ibge": fonte.municipio_ibge,
                "tipo": fonte.tipo.value,
                "url": fonte.url,
                "tipo_sistema": fonte.tipo_sistema.value,
                "status_mapeamento": "mapeado",
                "ultima_coleta": datetime.now().isoformat(),
                "notas": fonte.notas,
            }
            await self.db.upsert_fonte(fonte_dict)

        return all_licitacoes, all_contratos, all_publicacoes

    async def _execute_roteiro(
        self,
        roteiro: dict[str, Any],
        fonte: FonteDados,
        dry_run: bool,
        max_pages: int,
    ) -> tuple[list[Licitacao], list[Contrato], list[PublicacaoDiario]]:
        """Executa um roteiro de coleta completo, incluindo paginação."""
        licitacoes: list[Licitacao] = []
        contratos: list[Contrato] = []
        publicacoes: list[PublicacaoDiario] = []

        formato = roteiro.get("formato_final", "html_tabela")
        passos = roteiro.get("passos", [])
        iteracao = roteiro.get("iteracao", {})
        base_confidence = CONFIDENCE_MAP.get(formato, 0.65)

        if not passos:
            return [], [], []

        primeiro_passo = passos[0]
        base_url = primeiro_passo.get("url", "")
        if not base_url:
            return [], [], []

        # Estratégia de iteração
        iter_tipo = iteracao.get("tipo", "paginacao")
        param_pagina = iteracao.get("parametro_pagina", "page")
        itens_por_pagina = iteracao.get("itens_por_pagina", 20)

        page = 0
        while page < max_pages:
            # Construir URL da página atual
            url = self._build_url(base_url, param_pagina, page, itens_por_pagina, iter_tipo)

            # Executar passos do roteiro para esta iteração
            content, content_type = await self._execute_passos(passos, url, fonte.url)

            if not content:
                break  # Sem conteúdo — fim da paginação

            # Extrair dados conforme formato
            raw_items = await self._extract_by_format(
                content, content_type, formato, passos, fonte.url
            )

            if not raw_items:
                break  # Sem itens — fim da paginação

            # Converter para modelos Pydantic
            for item in raw_items.get("licitacoes", []):
                lic = self._to_licitacao(item, fonte, base_confidence)
                licitacoes.append(lic)
                if not dry_run:
                    await self.db.insert_licitacao(lic.model_dump(mode="json"))

            for item in raw_items.get("contratos", []):
                ct = self._to_contrato(item, fonte, base_confidence)
                contratos.append(ct)
                if not dry_run:
                    await self.db.insert_contrato(ct.model_dump(mode="json"))

            for item in raw_items.get("outros_atos", []):
                pub = self._to_publicacao(item, fonte)
                publicacoes.append(pub)
                if not dry_run:
                    await self.db.insert_publicacao(pub.model_dump(mode="json"))

            # Verificar se há próxima página
            total_itens = raw_items.get("total_itens_extraidos", 0)
            if iter_tipo == "paginacao":
                if total_itens < itens_por_pagina:
                    break  # Última página
                page += 1
            else:
                break  # Sem paginação — apenas uma iteração

            # Rate limiting entre páginas
            await asyncio.sleep(settings.RATE_LIMIT_DELAY_SECONDS)

        return licitacoes, contratos, publicacoes

    def _build_url(
        self,
        base_url: str,
        param_pagina: str | None,
        page: int,
        itens_por_pagina: int,
        iter_tipo: str,
    ) -> str:
        """Constrói a URL para uma página específica."""
        from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

        if iter_tipo != "paginacao" or not param_pagina or page == 0:
            return base_url

        parsed = urlparse(base_url)
        params = parse_qs(parsed.query)
        if param_pagina in ("offset",):
            params[param_pagina] = [str(page * itens_por_pagina)]
        else:
            params[param_pagina] = [str(page + 1)]  # maioria usa 1-indexed

        new_query = "&".join(f"{k}={v[0]}" for k, v in params.items())
        return urlunparse(parsed._replace(query=new_query))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=False,
    )
    async def _execute_passos(
        self,
        passos: list[dict[str, Any]],
        url: str,
        base_url: str,
    ) -> tuple[bytes | str, str]:
        """Executa os passos de uma requisição e retorna o conteúdo."""
        try:
            async with httpx.AsyncClient(
                timeout=30.0, headers=HEADERS, follow_redirects=True
            ) as client:
                primeiro = passos[0]
                metodo = primeiro.get("metodo", "GET").upper()
                body = primeiro.get("body")
                body_format = primeiro.get("body_format", "form-urlencoded")

                if metodo == "GET":
                    resp = await client.get(url)
                elif metodo == "POST":
                    if body_format == "json":
                        resp = await client.post(url, json=json.loads(body) if isinstance(body, str) else body)
                    else:
                        resp = await client.post(url, data=body or {})
                else:
                    resp = await client.get(url)

                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").lower()

                if "pdf" in content_type or url.lower().endswith(".pdf"):
                    return resp.content, "pdf"
                elif "json" in content_type:
                    return resp.text, "json"
                elif "csv" in content_type or url.lower().endswith(".csv"):
                    return resp.text, "csv"
                elif "excel" in content_type or url.lower().endswith((".xls", ".xlsx")):
                    return resp.content, "xls"
                else:
                    return resp.text, "html"

        except Exception as e:
            self.logger.debug(f"[Minerador] Requisição falhou para {url}: {e}")
            return b"", "error"

    async def _extract_by_format(
        self,
        content: bytes | str,
        content_type: str,
        formato: str,
        passos: list[dict[str, Any]],
        base_url: str,
    ) -> dict[str, Any]:
        """Extrai dados conforme o formato do conteúdo."""
        if content_type == "error" or not content:
            return {}

        # JSON API — parsing direto, sem LLM
        if content_type == "json" or formato == "json_api":
            return self._extract_from_json(content if isinstance(content, str) else content.decode())

        # CSV — parsing direto
        if content_type == "csv" or formato == "csv_download":
            return self._extract_from_csv(content if isinstance(content, str) else content.decode())

        # XLS/XLSX — pandas
        if content_type == "xls" or formato == "xls_download":
            return await asyncio.to_thread(
                self._extract_from_xls, content if isinstance(content, bytes) else content.encode()
            )

        # PDF — pdfplumber + LLM
        if content_type == "pdf" or formato == "pdf_download":
            text = await asyncio.to_thread(
                self.pdf_extractor.extract_text,
                content if isinstance(content, bytes) else content.encode(),
            )
            return await asyncio.to_thread(
                self.llm_extractor.extract_procurement_data_chunked, text
            )

        # HTML com seletores CSS do roteiro
        if content_type == "html" and passos:
            seletor = None
            campos = {}
            for passo in passos:
                if passo.get("seletor_dados"):
                    seletor = passo["seletor_dados"]
                if passo.get("campos_mapeados"):
                    campos = passo["campos_mapeados"]
                    break

            if seletor and campos:
                rows = self.html_extractor.extract_table(
                    content if isinstance(content, str) else content.decode(),
                    css_selector=seletor,
                )
                if rows:
                    return await asyncio.to_thread(
                        self.llm_extractor.extract_procurement_data,
                        json.dumps(rows, ensure_ascii=False),
                    )

            # HTML sem seletores — enviar texto ao LLM
            text = self.html_extractor.extract_text(
                content if isinstance(content, str) else content.decode()
            )
            return await asyncio.to_thread(
                self.llm_extractor.extract_procurement_data_chunked, text
            )

        # Texto corrido
        text = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
        return await asyncio.to_thread(
            self.llm_extractor.extract_procurement_data_chunked, text
        )

    def _extract_from_json(self, json_text: str) -> dict[str, Any]:
        """Extrai dados de uma resposta JSON de API."""
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            return {}

        # Tentar identificar a estrutura e normalizar
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # Procurar lista de itens em campos comuns
            for key in ("items", "data", "records", "licitacoes", "contratos", "content", "result"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
            if not items and data:
                items = [data]

        if not items:
            return {"licitacoes": [], "contratos": [], "outros_atos": [], "total_itens_extraidos": 0}

        # Usar LLM para normalizar o JSON para nossos modelos
        return self.llm_extractor.extract_procurement_data(
            json.dumps(items[:50], ensure_ascii=False)
        )

    def _extract_from_csv(self, csv_text: str) -> dict[str, Any]:
        """Extrai dados de um CSV."""
        try:
            reader = csv.DictReader(io.StringIO(csv_text))
            rows = [dict(row) for row in reader]
            if not rows:
                return {}
            # Usar LLM para normalizar o CSV
            return self.llm_extractor.extract_procurement_data(
                json.dumps(rows[:100], ensure_ascii=False)
            )
        except Exception as e:
            self.logger.error(f"[Minerador] Erro ao parsear CSV: {e}")
            return {}

    def _extract_from_xls(self, xls_bytes: bytes) -> dict[str, Any]:
        """Extrai dados de uma planilha Excel."""
        try:
            import pandas as pd  # type: ignore[import]
            df = pd.read_excel(io.BytesIO(xls_bytes))
            rows = df.fillna("").to_dict(orient="records")
            if not rows:
                return {}
            return self.llm_extractor.extract_procurement_data(
                json.dumps(rows[:100], ensure_ascii=False)
            )
        except Exception as e:
            self.logger.error(f"[Minerador] Erro ao parsear XLS: {e}")
            return {}

    async def _mine_querido_diario(
        self, fonte: FonteDados, dry_run: bool
    ) -> tuple[list[Licitacao], list[Contrato], list[PublicacaoDiario]]:
        """Coleta dados do Querido Diário via API."""
        from integrations.querido_diario import QueriDiarioClient
        qd = QueriDiarioClient(base_url=settings.QUERIDO_DIARIO_API_URL)

        try:
            result = await qd.search_gazettes(
                fonte.municipio_ibge,
                query="licitação OR contrato OR pregão OR dispensa",
                page_size=20,
            )
            publicacoes: list[PublicacaoDiario] = []
            for gazette in result.get("gazettes", []):
                pub = PublicacaoDiario(
                    id=str(uuid.uuid4()),
                    municipio_ibge=fonte.municipio_ibge,
                    fonte_id=fonte.id or str(uuid.uuid4()),
                    data_publicacao=_parse_date(gazette.get("date", "")) or date.today(),
                    tipo_ato="diario_oficial",
                    ementa=gazette.get("edition_number"),
                    texto_completo=None,
                    url_origem=gazette.get("file_url") or gazette.get("url"),
                    confianca_extracao=0.90,
                )
                publicacoes.append(pub)
                if not dry_run:
                    await self.db.insert_publicacao(pub.model_dump(mode="json"))

            return [], [], publicacoes
        except Exception as e:
            self.logger.error(f"[Minerador] Erro no Querido Diário: {e}")
            return [], [], []

    def _to_licitacao(
        self, item: dict[str, Any], fonte: FonteDados, base_confidence: float
    ) -> Licitacao:
        """Converte um dict extraído pelo LLM em objeto Licitacao."""
        confidence = float(item.get("confianca", base_confidence))
        return Licitacao(
            id=str(uuid.uuid4()),
            municipio_ibge=fonte.municipio_ibge,
            fonte_id=fonte.id or "",
            numero=item.get("numero"),
            modalidade=item.get("modalidade"),
            objeto=str(item.get("objeto") or ""),
            valor_estimado=_parse_float(item.get("valor_estimado")),
            valor_contratado=_parse_float(item.get("valor_contratado")),
            data_abertura=_parse_date(item.get("data_abertura")),
            data_publicacao=_parse_date(item.get("data_publicacao")),
            situacao=item.get("situacao"),
            vencedor_nome=item.get("vencedor_nome"),
            vencedor_cnpj=item.get("vencedor_cnpj"),
            url_origem=item.get("url_origem", fonte.url),
            texto_original=item.get("texto_fonte"),
            confianca_extracao=confidence,
        )

    def _to_contrato(
        self, item: dict[str, Any], fonte: FonteDados, base_confidence: float
    ) -> Contrato:
        """Converte um dict extraído pelo LLM em objeto Contrato."""
        confidence = float(item.get("confianca", base_confidence))
        return Contrato(
            id=str(uuid.uuid4()),
            municipio_ibge=fonte.municipio_ibge,
            fonte_id=fonte.id or "",
            numero=item.get("numero"),
            licitacao_numero=item.get("licitacao_numero"),
            objeto=str(item.get("objeto") or ""),
            contratado_nome=item.get("contratado_nome"),
            contratado_cnpj=item.get("contratado_cnpj"),
            valor=_parse_float(item.get("valor")),
            data_assinatura=_parse_date(item.get("data_assinatura")),
            data_inicio=_parse_date(item.get("data_inicio")),
            data_fim=_parse_date(item.get("data_fim")),
            url_origem=item.get("url_origem", fonte.url),
            texto_original=item.get("texto_fonte"),
            confianca_extracao=confidence,
        )

    def _to_publicacao(
        self, item: dict[str, Any], fonte: FonteDados
    ) -> PublicacaoDiario:
        """Converte um dict de outro ato em objeto PublicacaoDiario."""
        return PublicacaoDiario(
            id=str(uuid.uuid4()),
            municipio_ibge=fonte.municipio_ibge,
            fonte_id=fonte.id or "",
            data_publicacao=_parse_date(item.get("data")) or date.today(),
            tipo_ato=item.get("tipo"),
            ementa=item.get("ementa"),
            texto_completo=item.get("texto_fonte"),
            url_origem=item.get("url_origem", fonte.url),
            confianca_extracao=float(item.get("confianca", 0.5)),
        )


def _parse_float(value: Any) -> float | None:
    """Converte um valor para float, ignorando erros."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            # Remove R$, pontos de milhar, troca vírgula decimal
            cleaned = value.replace("R$", "").replace(".", "").replace(",", ".").strip()
            return float(cleaned)
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_date(value: Any) -> date | None:
    """Converte string para date. Tenta múltiplos formatos."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None
