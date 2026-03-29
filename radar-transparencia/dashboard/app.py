"""Dashboard Streamlit para visualizar o progresso do Radar Transparência."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

from config.settings import settings
from core.database import Database
from core.logger import setup_logging

setup_logging(settings.LOG_LEVEL)

st.set_page_config(
    page_title="Radar Transparência",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Helpers assíncronos ────────────────────────────────────────────────────


def _run_async(coro):
    """Executa corrotina em loop de eventos isolado (compatível com Streamlit)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@st.cache_data(ttl=60)
def get_stats() -> dict:
    async def _():
        db = Database(settings.db_path)
        await db.connect()
        try:
            return await db.get_stats()
        finally:
            await db.close()
    return _run_async(_())


@st.cache_data(ttl=60)
def get_municipios(uf: str | None = None, status: str | None = None) -> list[dict]:
    async def _():
        db = Database(settings.db_path)
        await db.connect()
        try:
            return await db.list_municipios(uf=uf, status=status, limit=500)
        finally:
            await db.close()
    return _run_async(_())


@st.cache_data(ttl=60)
def get_anomalias(municipio_ibge: str | None = None, limit: int = 200) -> list[dict]:
    async def _():
        db = Database(settings.db_path)
        await db.connect()
        try:
            return await db.list_anomalias(municipio_ibge=municipio_ibge, limit=limit)
        finally:
            await db.close()
    return _run_async(_())


@st.cache_data(ttl=60)
def get_licitacoes(municipio_ibge: str, limit: int = 100) -> list[dict]:
    async def _():
        db = Database(settings.db_path)
        await db.connect()
        try:
            return await db.list_licitacoes(municipio_ibge=municipio_ibge, limit=limit)
        finally:
            await db.close()
    return _run_async(_())


# ── Layout ────────────────────────────────────────────────────────────────


st.title("🔍 Radar Transparência")
st.caption("Sistema multi-agente de monitoramento da transparência pública municipal")

# ── Sidebar ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("Filtros")
    uf_options = [None, "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS",
                  "MG","PA","PB","PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO"]
    uf_labels = ["Todos os estados"] + [uf for uf in uf_options if uf]
    uf_idx = st.selectbox("Estado (UF)", range(len(uf_labels)), format_func=lambda i: uf_labels[i])
    selected_uf = uf_options[uf_idx]

    status_opts = [None, "pendente", "encontrado", "nao_encontrado", "erro"]
    status_labels = ["Todos os status", "Pendente", "Encontrado", "Não encontrado", "Erro"]
    status_idx = st.selectbox("Status", range(len(status_labels)), format_func=lambda i: status_labels[i])
    selected_status = status_opts[status_idx]

    st.divider()
    st.caption(f"DB: `{settings.db_path}`")
    if st.button("🔄 Atualizar dados"):
        st.cache_data.clear()
        st.rerun()

# ── Stats Cards ────────────────────────────────────────────────────────────

stats = get_stats()
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Municípios cadastrados", stats.get("total_municipios", 0))
col2.metric("Municípios cobertos", stats.get("municipios_cobertos", 0))
col3.metric("Licitações coletadas", stats.get("total_licitacoes", 0))
col4.metric("Contratos coletados", stats.get("total_contratos", 0))
col5.metric("Anomalias detectadas", stats.get("total_anomalias", 0))

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────

tab_municipios, tab_anomalias, tab_licitacoes = st.tabs(
    ["🏙️ Municípios", "⚠️ Anomalias", "📋 Licitações"]
)

# ── Tab Municípios ────────────────────────────────────────────────────────

with tab_municipios:
    municipios = get_municipios(uf=selected_uf, status=selected_status)

    if not municipios:
        st.info("Nenhum município encontrado. Execute o seed primeiro:\n```\npython scripts/seed_municipios.py --seed-only\n```")
    else:
        # Mapa de cobertura (se plotly disponível)
        try:
            import plotly.express as px
            import pandas as pd

            df = pd.DataFrame(municipios)
            status_counts = df.groupby(["uf", "status_descoberta"]).size().reset_index(name="count")
            fig = px.bar(
                status_counts,
                x="uf",
                y="count",
                color="status_descoberta",
                title=f"Municípios por UF e Status ({len(municipios)} total)",
                color_discrete_map={
                    "pendente": "#aaaaaa",
                    "encontrado": "#2ecc71",
                    "nao_encontrado": "#e74c3c",
                    "erro": "#f39c12",
                },
                labels={"uf": "Estado", "count": "Municípios", "status_descoberta": "Status"},
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)
        except ImportError:
            st.info("Instale plotly e pandas para visualizações: `pip install plotly pandas`")

        # Tabela de municípios
        st.subheader(f"Lista de Municípios ({len(municipios)})")
        cols_to_show = ["codigo_ibge", "nome", "uf", "populacao", "status_descoberta"]
        display_data = [{k: m.get(k, "") for k in cols_to_show} for m in municipios]
        st.dataframe(
            display_data,
            column_config={
                "codigo_ibge": "IBGE",
                "nome": "Município",
                "uf": "UF",
                "populacao": st.column_config.NumberColumn("População", format="%d"),
                "status_descoberta": "Status",
            },
            use_container_width=True,
            hide_index=True,
        )

# ── Tab Anomalias ─────────────────────────────────────────────────────────

with tab_anomalias:
    anomalias = get_anomalias()

    if not anomalias:
        st.success("Nenhuma anomalia detectada ainda.")
    else:
        # Resumo por tipo e severidade
        try:
            import plotly.express as px
            import pandas as pd

            df_anom = pd.DataFrame(anomalias)
            fig_sev = px.pie(
                df_anom,
                names="severidade",
                title="Anomalias por Severidade",
                color="severidade",
                color_discrete_map={
                    "baixa": "#3498db",
                    "media": "#f39c12",
                    "alta": "#e74c3c",
                    "critica": "#8e44ad",
                },
            )
            col_pie1, col_pie2 = st.columns(2)
            col_pie1.plotly_chart(fig_sev, use_container_width=True)

            fig_tipo = px.bar(
                df_anom.groupby("tipo").size().reset_index(name="count").sort_values("count", ascending=False),
                x="tipo",
                y="count",
                title="Anomalias por Tipo",
                labels={"tipo": "Tipo", "count": "Ocorrências"},
            )
            col_pie2.plotly_chart(fig_tipo, use_container_width=True)
        except ImportError:
            pass

        st.subheader(f"Lista de Anomalias ({len(anomalias)})")
        for anom in anomalias[:50]:
            sev = anom.get("severidade", "baixa")
            icon = {"critica": "🔴", "alta": "🟠", "media": "🟡", "baixa": "🔵"}.get(sev, "⚪")
            with st.expander(f"{icon} [{sev.upper()}] {anom.get('tipo')} — {anom.get('municipio_ibge')}"):
                st.write(anom.get("descricao"))
                if anom.get("dados_referencia"):
                    st.json(anom["dados_referencia"])

# ── Tab Licitações ────────────────────────────────────────────────────────

with tab_licitacoes:
    # Selector de município
    municipios_all = get_municipios(uf=selected_uf)
    municipio_options = {f"{m['nome']} ({m['uf']})": m["codigo_ibge"] for m in municipios_all}

    if not municipio_options:
        st.info("Nenhum município cadastrado. Execute o seed primeiro.")
    else:
        selected_municipio_label = st.selectbox(
            "Selecione o município", list(municipio_options.keys())
        )
        selected_ibge = municipio_options[selected_municipio_label]

        licitacoes = get_licitacoes(selected_ibge)
        if not licitacoes:
            st.info(f"Nenhuma licitação coletada para {selected_municipio_label}.")
        else:
            st.subheader(f"{len(licitacoes)} licitações — {selected_municipio_label}")
            cols_lic = [
                "numero", "modalidade", "objeto", "valor_estimado",
                "data_abertura", "situacao", "vencedor_nome",
            ]
            display_lics = [{k: l.get(k, "") for k in cols_lic} for l in licitacoes]
            st.dataframe(
                display_lics,
                column_config={
                    "numero": "Número",
                    "modalidade": "Modalidade",
                    "objeto": "Objeto",
                    "valor_estimado": st.column_config.NumberColumn("Valor Est. (R$)", format="R$ %.2f"),
                    "data_abertura": "Data Abertura",
                    "situacao": "Situação",
                    "vencedor_nome": "Vencedor",
                },
                use_container_width=True,
                hide_index=True,
            )
