"""Extrator de texto e tabelas de arquivos PDF."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Union

from core.logger import get_logger

logger = get_logger("PDFExtractor")


class PDFExtractor:
    """Extrai texto e tabelas estruturadas de arquivos PDF usando pdfplumber."""

    def extract_text(self, source: Union[str, bytes, Path]) -> str:
        """Extrai todo o texto de um PDF.

        Args:
            source: Caminho do arquivo, bytes do PDF ou objeto Path.

        Returns:
            Texto extraído concatenado, ou string vazia em caso de erro.
        """
        try:
            import pdfplumber  # type: ignore[import]
        except ImportError:
            logger.error("pdfplumber não instalado. Execute: pip install pdfplumber")
            return ""

        try:
            if isinstance(source, (str, Path)):
                pdf_file = open(source, "rb")
                close_after = True
            else:
                pdf_file = io.BytesIO(source)
                close_after = False

            pages_text: list[str] = []
            with pdfplumber.open(pdf_file) as pdf:
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    if text.strip():
                        pages_text.append(f"[Página {i + 1}]\n{text}")

            if close_after:
                pdf_file.close()  # type: ignore[union-attr]

            return "\n\n".join(pages_text)

        except Exception as e:
            logger.error(f"Erro ao extrair texto do PDF: {e}")
            return ""

    def extract_text_pages(
        self, source: Union[str, bytes, Path]
    ) -> list[tuple[int, str]]:
        """Extrai texto página por página.

        Args:
            source: Caminho do arquivo, bytes do PDF ou objeto Path.

        Returns:
            Lista de tuplas (numero_pagina, texto), onde numero_pagina começa em 1.
        """
        try:
            import pdfplumber  # type: ignore[import]
        except ImportError:
            logger.error("pdfplumber não instalado.")
            return []

        try:
            if isinstance(source, (str, Path)):
                pdf_file = open(source, "rb")
                close_after = True
            else:
                pdf_file = io.BytesIO(source)
                close_after = False

            result: list[tuple[int, str]] = []
            with pdfplumber.open(pdf_file) as pdf:
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    result.append((i + 1, text))

            if close_after:
                pdf_file.close()  # type: ignore[union-attr]

            return result

        except Exception as e:
            logger.error(f"Erro ao extrair páginas do PDF: {e}")
            return []

    def extract_tables(
        self, source: Union[str, bytes, Path]
    ) -> list[list[list[str | None]]]:
        """Extrai tabelas estruturadas de um PDF.

        Args:
            source: Caminho do arquivo, bytes do PDF ou objeto Path.

        Returns:
            Lista de tabelas, onde cada tabela é uma lista de linhas,
            e cada linha é uma lista de células (str ou None).
        """
        try:
            import pdfplumber  # type: ignore[import]
        except ImportError:
            logger.error("pdfplumber não instalado.")
            return []

        try:
            if isinstance(source, (str, Path)):
                pdf_file = open(source, "rb")
                close_after = True
            else:
                pdf_file = io.BytesIO(source)
                close_after = False

            all_tables: list[list[list[str | None]]] = []
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables:
                        all_tables.extend(tables)

            if close_after:
                pdf_file.close()  # type: ignore[union-attr]

            return all_tables

        except Exception as e:
            logger.error(f"Erro ao extrair tabelas do PDF: {e}")
            return []

    def extract_metadata(self, source: Union[str, bytes, Path]) -> dict[str, str]:
        """Extrai metadados de um PDF (título, autor, data de criação, etc.).

        Args:
            source: Caminho do arquivo, bytes do PDF ou objeto Path.

        Returns:
            Dicionário de metadados.
        """
        try:
            import pdfplumber  # type: ignore[import]
        except ImportError:
            return {}

        try:
            if isinstance(source, (str, Path)):
                pdf_file = open(source, "rb")
                close_after = True
            else:
                pdf_file = io.BytesIO(source)
                close_after = False

            with pdfplumber.open(pdf_file) as pdf:
                meta = pdf.metadata or {}

            if close_after:
                pdf_file.close()  # type: ignore[union-attr]

            return {k: str(v) for k, v in meta.items() if v}

        except Exception as e:
            logger.error(f"Erro ao extrair metadados do PDF: {e}")
            return {}
