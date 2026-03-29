"""Classe base para todos os agentes do sistema Radar Transparência."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from core.database import Database
from core.logger import get_logger


class BaseAgent(ABC):
    """Classe base para todos os agentes do sistema.

    Fornece acesso ao LLM, parsing de respostas JSON, e rastreamento de stats.
    """

    def __init__(self, db: Database, model: str = "claude-sonnet-4-20250514") -> None:
        """Inicializa o agente.

        Args:
            db: Instância do banco de dados.
            model: ID do modelo Anthropic a usar.
        """
        self.db = db
        self.client = anthropic.Anthropic()
        self.model = model
        self.logger = get_logger(self.__class__.__name__)
        self.stats: dict[str, int] = {
            "processados": 0,
            "sucesso": 0,
            "erro": 0,
            "ignorados": 0,
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def call_llm(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 4096,
    ) -> str:
        """Chamada ao LLM com retry automático.

        Args:
            system_prompt: Prompt de sistema.
            user_message: Mensagem do usuário.
            max_tokens: Limite de tokens na resposta.

        Returns:
            Texto da resposta do LLM.
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def call_llm_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> anthropic.types.Message:
        """Chamada ao LLM com tools (function calling) e retry automático.

        Args:
            system_prompt: Prompt de sistema.
            user_message: Mensagem do usuário.
            tools: Lista de tool definitions no formato Anthropic.
            max_tokens: Limite de tokens na resposta.

        Returns:
            Objeto Message completo do Anthropic SDK.
        """
        return self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=tools,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": user_message}],
        )

    def call_llm_with_tools_loop(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict[str, Any]],
        tool_handler: "ToolHandler",
        max_tokens: int = 4096,
        max_iterations: int = 10,
    ) -> str:
        """Executa o loop completo de tool use até obter uma resposta final.

        Chama o LLM, executa as tools requisitadas pelo modelo, e continua
        até que o modelo pare de requisitar tools ou o limite de iterações
        seja atingido.

        Args:
            system_prompt: Prompt de sistema.
            user_message: Mensagem do usuário.
            tools: Definições das tools disponíveis.
            tool_handler: Objeto que implementa handle(tool_name, tool_input) -> str.
            max_tokens: Limite de tokens por chamada.
            max_iterations: Número máximo de iterações do loop.

        Returns:
            Texto final da resposta do LLM.
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        final_text = ""

        for _ in range(max_iterations):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt,
                tools=tools,  # type: ignore[arg-type]
                messages=messages,
            )

            # Coletar texto da resposta atual
            for block in response.content:
                if hasattr(block, "text"):
                    final_text = block.text

            # Se parou por end_turn ou max_tokens, encerrar loop
            if response.stop_reason in ("end_turn", "max_tokens"):
                break

            # Processar tool calls
            if response.stop_reason == "tool_use":
                # Adicionar resposta do assistente ao histórico
                messages.append({"role": "assistant", "content": response.content})

                # Processar cada tool use e coletar resultados
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        try:
                            result = tool_handler.handle(block.name, block.input)
                        except Exception as e:
                            result = f"Erro ao executar tool {block.name}: {e}"
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )

                messages.append({"role": "user", "content": tool_results})
            else:
                break

        return final_text

    def parse_json_response(self, text: str) -> dict[str, Any]:
        """Extrai JSON de uma resposta do LLM, mesmo que contenha texto ao redor.

        Suporta JSON puro, JSON dentro de blocos de código markdown,
        e JSON embutido em texto livre.

        Args:
            text: Texto bruto da resposta do LLM.

        Returns:
            Dicionário parsed ou dict vazio em caso de falha.
        """
        text = text.strip()

        # Remove blocos de código markdown
        for prefix in ("```json\n", "```json", "```\n", "```"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Tenta parse direto
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            pass

        # Tenta encontrar o primeiro objeto JSON {}
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        # Tenta encontrar o primeiro array JSON []
        start = text.find("[")
        end = text.rfind("]") + 1
        if start != -1 and end > start:
            try:
                result = json.loads(text[start:end])
                return {"items": result}
            except json.JSONDecodeError:
                pass

        self.logger.error(f"Falha ao parsear JSON da resposta LLM: {text[:300]}...")
        return {}

    def log_stats(self) -> None:
        """Loga as estatísticas de execução do agente."""
        self.logger.info(f"Estatísticas: {json.dumps(self.stats, ensure_ascii=False)}")

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """Método principal de execução do agente. Implementado por subclasses."""
        ...


class ToolHandler:
    """Interface para handlers de tools no loop de tool use."""

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Executa uma tool e retorna o resultado como string.

        Args:
            tool_name: Nome da tool a executar.
            tool_input: Parâmetros da tool.

        Returns:
            Resultado da execução como string.
        """
        raise NotImplementedError(f"Tool '{tool_name}' não implementada")
