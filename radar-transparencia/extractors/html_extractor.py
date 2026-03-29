"""Extrator de dados de páginas HTML usando BeautifulSoup."""

from __future__ import annotations

from typing import Any

from core.logger import get_logger

logger = get_logger("HTMLExtractor")


class HTMLExtractor:
    """Extrai dados estruturados de páginas HTML usando BeautifulSoup e CSS selectors."""

    def extract_table(
        self,
        html: str,
        css_selector: str = "table",
        header_row: bool = True,
    ) -> list[dict[str, Any]]:
        """Extrai dados de uma tabela HTML como lista de dicionários.

        Args:
            html: HTML da página.
            css_selector: Seletor CSS para localizar a tabela.
            header_row: Se True, usa a primeira linha como cabeçalho.

        Returns:
            Lista de dicionários, um por linha da tabela.
        """
        try:
            from bs4 import BeautifulSoup  # type: ignore[import]
        except ImportError:
            logger.error("beautifulsoup4 não instalado.")
            return []

        try:
            soup = BeautifulSoup(html, "lxml")
            table = soup.select_one(css_selector)
            if not table:
                # Tentar com seletor mais genérico
                table = soup.find("table")
            if not table:
                return []

            rows = table.find_all("tr")
            if not rows:
                return []

            if header_row:
                # Extrair cabeçalhos da primeira linha (th ou td)
                header_cells = rows[0].find_all(["th", "td"])
                headers = [
                    cell.get_text(strip=True) or f"col_{i}"
                    for i, cell in enumerate(header_cells)
                ]
                data_rows = rows[1:]
            else:
                headers = [f"col_{i}" for i in range(len(rows[0].find_all(["th", "td"])))]
                data_rows = rows

            result: list[dict[str, Any]] = []
            for row in data_rows:
                cells = row.find_all(["td", "th"])
                if not cells:
                    continue
                row_dict: dict[str, Any] = {}
                for i, cell in enumerate(cells):
                    key = headers[i] if i < len(headers) else f"col_{i}"
                    # Extrair links se presentes
                    link = cell.find("a")
                    row_dict[key] = cell.get_text(strip=True)
                    if link and link.get("href"):
                        row_dict[f"{key}_href"] = link["href"]
                result.append(row_dict)

            return result

        except Exception as e:
            logger.error(f"Erro ao extrair tabela HTML: {e}")
            return []

    def extract_tables_all(self, html: str) -> list[list[dict[str, Any]]]:
        """Extrai todas as tabelas de um HTML.

        Args:
            html: HTML da página.

        Returns:
            Lista de tabelas, onde cada tabela é uma lista de dicionários.
        """
        try:
            from bs4 import BeautifulSoup  # type: ignore[import]
        except ImportError:
            return []

        try:
            soup = BeautifulSoup(html, "lxml")
            tables = soup.find_all("table")
            result = []
            for table in tables:
                rows = table.find_all("tr")
                if not rows:
                    continue
                header_cells = rows[0].find_all(["th", "td"])
                headers = [
                    cell.get_text(strip=True) or f"col_{i}"
                    for i, cell in enumerate(header_cells)
                ]
                table_data = []
                for row in rows[1:]:
                    cells = row.find_all(["td", "th"])
                    if not cells:
                        continue
                    row_dict = {}
                    for i, cell in enumerate(cells):
                        key = headers[i] if i < len(headers) else f"col_{i}"
                        row_dict[key] = cell.get_text(strip=True)
                        link = cell.find("a")
                        if link and link.get("href"):
                            row_dict[f"{key}_href"] = link["href"]
                    table_data.append(row_dict)
                if table_data:
                    result.append(table_data)
            return result
        except Exception as e:
            logger.error(f"Erro ao extrair tabelas HTML: {e}")
            return []

    def extract_links(self, html: str, css_selector: str = "a") -> list[str]:
        """Extrai todos os hrefs de links que correspondem ao seletor CSS.

        Args:
            html: HTML da página.
            css_selector: Seletor CSS para localizar os links.

        Returns:
            Lista de URLs (hrefs).
        """
        try:
            from bs4 import BeautifulSoup  # type: ignore[import]
        except ImportError:
            return []

        try:
            soup = BeautifulSoup(html, "lxml")
            links = soup.select(css_selector)
            return [
                link.get("href", "")
                for link in links
                if link.get("href") and link["href"].strip()
            ]
        except Exception as e:
            logger.error(f"Erro ao extrair links: {e}")
            return []

    def extract_text(self, html: str, css_selector: str = "body") -> str:
        """Extrai texto de um elemento selecionado no HTML.

        Args:
            html: HTML da página.
            css_selector: Seletor CSS para localizar o elemento.

        Returns:
            Texto extraído (strip de espaços).
        """
        try:
            from bs4 import BeautifulSoup  # type: ignore[import]
        except ImportError:
            return ""

        try:
            soup = BeautifulSoup(html, "lxml")
            elem = soup.select_one(css_selector)
            if not elem:
                elem = soup.find("body") or soup
            return elem.get_text(separator="\n", strip=True)  # type: ignore[union-attr]
        except Exception as e:
            logger.error(f"Erro ao extrair texto HTML: {e}")
            return ""

    def extract_fields(
        self, html: str, fields: dict[str, str]
    ) -> dict[str, str]:
        """Extrai campos específicos usando mapeamento de seletores CSS.

        Args:
            html: HTML da página.
            fields: Dicionário {nome_campo: seletor_css}.

        Returns:
            Dicionário {nome_campo: texto_extraído}.
        """
        try:
            from bs4 import BeautifulSoup  # type: ignore[import]
        except ImportError:
            return {}

        try:
            soup = BeautifulSoup(html, "lxml")
            result: dict[str, str] = {}
            for field_name, selector in fields.items():
                elem = soup.select_one(selector)
                result[field_name] = elem.get_text(strip=True) if elem else ""
            return result
        except Exception as e:
            logger.error(f"Erro ao extrair campos HTML: {e}")
            return {}

    def resolve_url(self, href: str, base_url: str) -> str:
        """Resolve uma URL relativa em absoluta.

        Args:
            href: URL relativa ou absoluta.
            base_url: URL base da página.

        Returns:
            URL absoluta.
        """
        from urllib.parse import urljoin
        return urljoin(base_url, href)
