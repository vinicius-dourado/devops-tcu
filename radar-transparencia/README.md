# Radar Transparência

Sistema multi-agente em Python para rastrear, descobrir, mapear, extrair e normalizar dados de transparência pública (licitações, contratações, diários oficiais, despesas) dos 5.570 municípios brasileiros.

## O Problema

Cada município publica seus dados de forma diferente — em portais com estruturas diversas, diários oficiais em PDF, sistemas de gestão variados (Betha, IPM, Fiorilli, Elotech), ou em associações de municípios. Não existe padronização.

Este sistema usa agentes de IA para superar essa heterogeneidade **sem necessidade de scrapers manuais para cada portal**. O objetivo é aprender o caminho completo até efetivamente baixar os dados — sejam eles CSVs, PDFs, tabelas HTML, respostas de APIs JSON, ou texto corrido de diários oficiais.

## Arquitetura: 4 Agentes Especializados

```
Scout → Cartógrafo → Minerador → Auditor
```

| Agente | Responsabilidade |
|--------|-----------------|
| **Scout** | Descobre URLs de portais de transparência via web search |
| **Cartógrafo** | Mapeia a estrutura do portal e gera roteiro de coleta passo a passo |
| **Minerador** | Segue o roteiro mecanicamente para extrair dados estruturados |
| **Auditor** | Valida CNPJs, normaliza dados e detecta anomalias |

## Estrutura do Projeto

```
radar-transparencia/
├── agents/                    # Agentes de IA
│   ├── base.py                # Classe base (LLM calls, JSON parsing)
│   ├── scout.py               # Descoberta de fontes
│   ├── cartographer.py        # Mapeamento de portais
│   ├── miner.py               # Extração de dados
│   ├── auditor.py             # Validação e anomalias
│   └── prompts/               # Prompts de cada agente
├── config/
│   ├── settings.py            # Configurações (.env)
│   └── municipios_seed.json   # 10 municípios para testes
├── core/
│   ├── models.py              # Modelos Pydantic (Licitacao, Contrato, etc.)
│   ├── database.py            # SQLite com aiosqlite
│   ├── logger.py              # Logger com rich
│   └── state.py               # Controle de retomada do pipeline
├── extractors/
│   ├── pdf_extractor.py       # Extração de PDFs (pdfplumber)
│   ├── html_extractor.py      # Extração de HTML (BeautifulSoup)
│   └── llm_extractor.py       # Extração via LLM para conteúdo não estruturado
├── integrations/
│   ├── ibge.py                # API IBGE (lista de municípios)
│   ├── querido_diario.py      # API Querido Diário
│   └── cnpj.py                # Validação de CNPJ
├── pipeline/
│   ├── orchestrator.py        # Orquestrador (executa os 4 agentes em sequência)
│   └── scheduler.py           # Agendamento periódico
├── api/
│   └── server.py              # API FastAPI para consultar dados
├── dashboard/
│   └── app.py                 # Dashboard Streamlit
├── scripts/
│   ├── seed_municipios.py     # Popular banco via API IBGE
│   └── run_pipeline.py        # CLI principal
└── tests/                     # Testes unitários
```

## Instalação

### Pré-requisitos

- Python 3.11+
- `ANTHROPIC_API_KEY` configurada

```bash
cd radar-transparencia

# Instalar dependências
pip install -e .

# Para portais com JavaScript (opcional):
pip install -e ".[browser]"
playwright install chromium

# Configurar variáveis de ambiente
cp .env.example .env
# Editar .env e preencher ANTHROPIC_API_KEY
```

## Uso Rápido

### 1. Popular o banco de dados

```bash
# Seed rápido: 10 municípios para testes
python scripts/seed_municipios.py --seed-only

# Todos os municípios de SP via API IBGE
python scripts/seed_municipios.py --uf SP

# Todos os 5.570 municípios
python scripts/seed_municipios.py
```

### 2. Executar o pipeline

```bash
# Teste: apenas Scout em 1 município (sem salvar)
python scripts/run_pipeline.py --municipio 3509502 --only-scout --dry-run

# Pipeline completo em 3 municípios de SP
python scripts/run_pipeline.py --uf SP --limit 3

# Pipeline completo com retomada automática
python scripts/run_pipeline.py --uf MG --batch-size 5 --resume

# Todos os municípios do banco
python scripts/run_pipeline.py --all --batch-size 20
```

### 3. Visualizar resultados

```bash
# Dashboard Streamlit
streamlit run dashboard/app.py

# API REST
uvicorn api.server:app --reload
# Acessar: http://localhost:8000/docs
```

## Opções do Pipeline

| Flag | Descrição |
|------|-----------|
| `--uf SP` | Filtrar por estado |
| `--limit 10` | Limitar número de municípios |
| `--municipio 3550308` | Município específico (código IBGE) |
| `--all` | Todos os municípios do banco |
| `--batch-size 5` | Municípios processados em paralelo |
| `--resume` | Pular etapas já concluídas (padrão) |
| `--only-scout` | Apenas etapa de descoberta |
| `--only-cartographer` | Scout + Cartógrafo |
| `--only-miner` | Scout + Cartógrafo + Minerador |
| `--dry-run` | Executar sem salvar no banco |
| `--output-report report.json` | Salvar relatório de execução |

## API REST

Com o servidor rodando em `http://localhost:8000`:

```
GET /stats                              → Estatísticas gerais
GET /municipios?uf=SP&limit=50          → Listar municípios
GET /municipios/{ibge}                  → Detalhes de um município
GET /municipios/{ibge}/licitacoes       → Licitações
GET /municipios/{ibge}/contratos        → Contratos
GET /anomalias?severidade=alta          → Anomalias detectadas
GET /docs                               → Documentação interativa (Swagger)
```

## Banco de Dados

O sistema usa **SQLite** por padrão (arquivo `radar_transparencia.db`). Para produção com PostgreSQL, altere no `.env`:

```env
DATABASE_URL=postgresql://user:pass@host:5432/radar_transparencia
```

## Tipos de Dados Suportados

O Cartógrafo e Minerador suportam os seguintes formatos:

| Formato | Confiança | Descrição |
|---------|-----------|-----------|
| `json_api` | 0.95 | API REST interna do portal |
| `csv_download` | 0.90 | Planilha CSV para download |
| `xls_download` | 0.88 | Planilha Excel |
| `html_tabela` | 0.85 | Tabela `<table>` paginada |
| `javascript_rendered` | 0.70 | Conteúdo carregado por JS (requer playwright) |
| `pdf_download` | 0.75 | PDF de licitação/contrato |
| `texto_corrido` | 0.65 | Diário oficial com texto livre |

## Sistemas de Gestão Reconhecidos

O Cartógrafo identifica automaticamente o sistema de gestão para reutilizar roteiros:

- **Betha** (betha.com.br, betha.cloud)
- **IPM** (ipm.com.br, atende.net)
- **Fiorilli** (fiorilli.com.br)
- **Elotech** (elotech.com.br)
- **Governa**, **Portal Fácil**, **e-Cidade**, e outros

## Anomalias Detectadas

| Tipo | Severidade | Descrição |
|------|-----------|-----------|
| `EMPRESA_FREQUENTE` | Alta | Mesma empresa vencendo >40% das licitações |
| `FRACIONAMENTO_SUSPEITO` | Alta | Múltiplas dispensas evitando licitação |
| `DISPENSA_VALOR_LIMITE` | Média | Dispensa com valor próximo ao teto legal |
| `CNPJ_INVALIDO` | Média | CNPJ com dígitos verificadores incorretos |
| `OBJETO_GENERICO` | Baixa | Objeto licitado descrito vagamente |

## Executar Testes

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Variáveis de Ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `ANTHROPIC_API_KEY` | — | **Obrigatória** |
| `DATABASE_URL` | `sqlite:///./radar_transparencia.db` | Banco de dados |
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Modelo Anthropic |
| `BATCH_SIZE` | `10` | Municípios por lote |
| `RATE_LIMIT_DELAY_SECONDS` | `2` | Pausa entre requisições |
| `USE_PLAYWRIGHT` | `false` | Habilitar suporte a JS |
| `LOG_LEVEL` | `INFO` | Nível de log |
| `LOG_FILE` | — | Arquivo de log (opcional) |

## Fontes de Dados

- **Querido Diário** (queridodiario.ok.org.br): diários oficiais de ~3.000 municípios
- **Portais de transparência municipais**: dados de licitações, contratos e despesas
- **API IBGE**: lista completa de 5.570 municípios com códigos e UFs

> Este sistema **não** integra com o PNCP (Portal Nacional de Contratações Públicas), que já é coberto por outro sistema. O foco é exclusivamente em fontes municipais que não estão no PNCP.

## Licença

Este é um projeto de impacto social para combater a falta de transparência e a corrupção no Brasil.
