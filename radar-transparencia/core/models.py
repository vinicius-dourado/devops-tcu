"""Modelos de dados do sistema Radar Transparência."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class StatusDescoberta(str, Enum):
    PENDENTE = "pendente"
    ENCONTRADO = "encontrado"
    NAO_ENCONTRADO = "nao_encontrado"
    ERRO = "erro"


class StatusMapeamento(str, Enum):
    PENDENTE = "pendente"
    MAPEADO = "mapeado"
    PARCIAL = "parcial"
    INACESSIVEL = "inacessivel"
    ERRO = "erro"


class TipoFonte(str, Enum):
    PORTAL_TRANSPARENCIA = "portal_transparencia"
    DIARIO_OFICIAL = "diario_oficial"
    PORTAL_LICITACOES = "portal_licitacoes"
    QUERIDO_DIARIO = "querido_diario"
    ASSOCIACAO_MUNICIPIOS = "associacao_municipios"
    OUTRO = "outro"


class TipoSistema(str, Enum):
    BETHA = "betha"
    IPM = "ipm"
    FIORILLI = "fiorilli"
    ELOTECH = "elotech"
    GOVERNA = "governa"
    PORTAL_FACIL = "portal_facil"
    CUSTOM = "custom"
    DESCONHECIDO = "desconhecido"


class Municipio(BaseModel):
    """Representa um município brasileiro."""

    codigo_ibge: str
    nome: str
    uf: str
    populacao: Optional[int] = None
    status_descoberta: StatusDescoberta = StatusDescoberta.PENDENTE
    fontes: list["FonteDados"] = Field(default_factory=list)
    ultima_atualizacao: Optional[datetime] = None


class FonteDados(BaseModel):
    """Representa uma fonte de dados de transparência de um município."""

    id: Optional[str] = None
    municipio_ibge: str
    tipo: TipoFonte
    url: str
    tipo_sistema: TipoSistema = TipoSistema.DESCONHECIDO
    status_mapeamento: StatusMapeamento = StatusMapeamento.PENDENTE
    mapa_navegacao: Optional[dict[str, Any]] = None
    ultima_coleta: Optional[datetime] = None
    notas: Optional[str] = None


class Licitacao(BaseModel):
    """Representa uma licitação municipal."""

    id: Optional[str] = None
    municipio_ibge: str
    fonte_id: str
    numero: Optional[str] = None
    modalidade: Optional[str] = None
    objeto: str
    valor_estimado: Optional[float] = None
    valor_contratado: Optional[float] = None
    data_abertura: Optional[date] = None
    data_publicacao: Optional[date] = None
    situacao: Optional[str] = None
    vencedor_nome: Optional[str] = None
    vencedor_cnpj: Optional[str] = None
    url_origem: Optional[str] = None
    texto_original: Optional[str] = None
    confianca_extracao: float = 0.0
    validado: bool = False
    data_coleta: datetime = Field(default_factory=datetime.now)


class Contrato(BaseModel):
    """Representa um contrato municipal."""

    id: Optional[str] = None
    municipio_ibge: str
    fonte_id: str
    numero: Optional[str] = None
    licitacao_numero: Optional[str] = None
    objeto: str
    contratado_nome: Optional[str] = None
    contratado_cnpj: Optional[str] = None
    valor: Optional[float] = None
    data_assinatura: Optional[date] = None
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    url_origem: Optional[str] = None
    texto_original: Optional[str] = None
    confianca_extracao: float = 0.0
    validado: bool = False
    data_coleta: datetime = Field(default_factory=datetime.now)


class PublicacaoDiario(BaseModel):
    """Representa uma publicação em diário oficial."""

    id: Optional[str] = None
    municipio_ibge: str
    fonte_id: str
    data_publicacao: date
    tipo_ato: Optional[str] = None
    ementa: Optional[str] = None
    texto_completo: Optional[str] = None
    url_origem: Optional[str] = None
    arquivo_original: Optional[str] = None
    confianca_extracao: float = 0.0
    data_coleta: datetime = Field(default_factory=datetime.now)


class Anomalia(BaseModel):
    """Representa uma anomalia detectada pelo Auditor."""

    id: Optional[str] = None
    municipio_ibge: str
    tipo: str
    descricao: str
    severidade: str
    dados_referencia: dict[str, Any] = Field(default_factory=dict)
    data_deteccao: datetime = Field(default_factory=datetime.now)


class ExecucaoLog(BaseModel):
    """Registro de uma execução do pipeline."""

    id: Optional[str] = None
    municipio_ibge: str
    etapa: str
    status: str
    mensagem: Optional[str] = None
    iniciado_em: datetime = Field(default_factory=datetime.now)
    finalizado_em: Optional[datetime] = None
    detalhes: Optional[dict[str, Any]] = None
