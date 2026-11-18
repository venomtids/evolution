"""
dashboard.py
Frontend Dashboard Operacional para manutenção preditiva e segurança industrial.

Interface Streamlit com:
  - Auto-refresh configurável (a cada 2 segundos)
  - Indicador visual de status (Verde = NORMAL, Amarelo = ALERTA, Vermelho Pulsante = CRITICO)
  - Telemetria: gráfico de linha em tempo real com índice de vibração ao longo do tempo
  - Linhas tracejadas indicando thresholds de ALERTA e CRITICO
  - Auditoria visual: tabela de ocorrências recentes com miniaturas dos frames capturados
  - Controles: PARADA DE EMERGÊNCIA MANUAL e RESET DO SISTEMA
  - Exportação de histórico filtrado em CSV

Arquitetura:
  - Conecta ao backend FastAPI via API REST
  - Busca eventos do banco de dados SQLite (caché local via st.cache_data)
  - Atualiza estado em tempo real sem recarregar a página
"""

import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
import requests

# Configuração da página
st.set_page_config(
    page_title="Industrial Maintenance Dashboard",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Token JWT opcional (para acesso a /diagnostics/* autenticado)
API_TOKEN = os.getenv("DASHBOARD_API_TOKEN", "")

# Import config local
from config import config

# ---------------------------------------------------------
# Configurações do Dashboard
# ---------------------------------------------------------

PAGE_TITLE = "Dashboard Operacional - Manutenção Preditiva"
REFRESH_SECONDS = config.DASHBOARD_REFRESH_SECONDS if hasattr(config, 'DASHBOARD_REFRESH_SECONDS') else 2
MAX_TELEMETRY_POINTS = config.MAX_TELEMETRY_POINTS if hasattr(config, 'MAX_TELEMETRY_POINTS') else 300
API_BASE = config.API_HOST if config.API_HOST != "0.0.0.0" else "http://127.0.0.1"

# ---------------------------------------------------------
# Estado da Sessão Streamlit
# ---------------------------------------------------------


def init_session_state():
    """Inicializa/limpa o estado da sessão."""
    if "monitor_running" not in st.session_state:
        st.session_state.monitor_running = False
    if "monitor" not in st.session_state:
        st.session_state.monitor = None
    if "last_status" not in st.session_state:
        st.session_state.last_status = "NORMAL"
    if "last_magnitude" not in st.session_state:
        st.session_state.last_magnitude = 0.0
    if "events_cache" not in st.session_state:
        st.session_state.events_cache = []
    if "db_connected" not in st.session_state:
        st.session_state.db_connected = False
    if "auto_refresh" not in st.session_state:
        st.session_state.auto_refresh = True


# ---------------------------------------------------------
# Funções de Utilitário
# ---------------------------------------------------------


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_events_cached(limit: int = 100, status_filter: Optional[str] = None) -> List[dict]:
    """Busca eventos do backend API com cache."""
    try:
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/events?page=1&page_size={limit}",
            params={"status": status_filter} if status_filter else {},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("events", [])
    except Exception as e:
        st.warning(f"Erro ao buscar eventos: {e}")
    return []


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_status_cached() -> dict:
    """Busca status atual da API."""
    try:
        resp = requests.get(f"{API_BASE}:{config.API_PORT}/status", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        st.warning(f"Erro ao buscar status: {e}")
    return {}


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_telemetry_cached(limit: int = 100) -> List[dict]:
    """Busca dados de telemetria."""
    try:
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/telemetry",
            params={"limit": limit},
            timeout=5
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("points", [])
    except Exception as e:
        st.warning(f"Erro ao buscar telemetria: {e}")
    return []


@st.cache_data(ttl=REFRESH_SECONDS)
def export_csv_cached(start_date=None, end_date=None, status_filter=None) -> bytes:
    """Exporta histórico em CSV."""
    try:
        params = {}
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()
        if status_filter:
            params["status"] = status_filter
        
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/export/csv",
            params=params,
            timeout=10
        )
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        st.warning(f"Erro ao exportar CSV: {e}")
    return b""


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_latest_diagnosis() -> dict:
    """Busca último laudo da IA (requer token)."""
    if not API_TOKEN:
        return {}
    try:
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/diagnostics/latest",
            headers=_auth_headers(),
            timeout=5
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Erro ao buscar diagnóstico: {e}")
    return {}


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_diagnostic_stats() -> dict:
    """Busca contador de erros na janela (requer token)."""
    if not API_TOKEN:
        return {}
    try:
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/diagnostics/stats",
            headers=_auth_headers(),
            timeout=5
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Erro ao buscar stats de IA: {e}")
    return {}


def render_ai_diagnosis():
    """Card de diagnóstico por IA + contador de erros."""
    st.markdown("### 🤖 Diagnóstico IA")
    stats = fetch_diagnostic_stats()
    diag = fetch_latest_diagnosis()

    if stats:
        col_a, col_b = st.columns(2)
        col_a.metric("Erros na janela", stats.get("errors_in_window", 0))
        col_b.metric("Limite burst", stats.get("burst_limit", "—"))
    elif not API_TOKEN:
        st.info("Configure DASHBOARD_API_TOKEN no .env para ver o diagnóstico IA (requer login JWT).")

    if diag:
        sev = diag.get("severidade", "media")
        colors = {"baixa": "#28a745", "media": "#ffc107", "alta": "#fd7e14", "critica": "#dc3545"}
        color = colors.get(sev, "#6c757d")
        st.markdown(
            f'<div style="border-left:4px solid {color};padding:8px 12px;background:#1e1e1e;'
            f'border-radius:4px;margin-top:6px;">'
            f'<div style="font-size:0.75em;color:#aaa;">Provider: {diag.get("provider")} | Confiança: {diag.get("confidence", 0):.0%}</div>'
            f'<div style="font-weight:bold;color:{color};">{sev.upper()}</div>'
            f'<div style="font-size:0.85em;">{diag.get("diagnostico", "")}</div>'
            f'<div style="font-size:0.8em;color:#ffd;">Causa provável: {diag.get("causa_provavel", "")}</div>'
            f'<div style="font-size:0.8em;">Recomendação: {diag.get("recomendacao", "")}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_ml_status() -> dict:
    """Metadados da IA treinada (IsolationForest + RandomForest)."""
    if not API_TOKEN:
        return {}
    try:
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/ml/status",
            headers=_auth_headers(),
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Erro ao buscar /ml/status: {e}")
    return {}


def render_ml_card():
    """Card da IA open-source treinada (scikit-learn)."""
    st.markdown("### 🧠 IA Treinada (open-source)")
    status = fetch_ml_status()
    if not status:
        if not API_TOKEN:
            st.info("Configure DASHBOARD_API_TOKEN para ver/acionar a IA treinada.")
        return
    if status.get("trained"):
        cls = status.get("classifier", {})
        acc = cls.get("train_accuracy")
        col1, col2 = st.columns(2)
        col1.metric("Janelas treinadas", status.get("n_windows", 0))
        col2.metric("Acurácia RF", f"{acc:.0%}" if acc is not None else "—")
        st.caption(
            f"Classes: {', '.join(cls.get('classes', [])) or '—'} · "
            f"IsolationForest (anomalias não-supervisionadas)\n\n"
            f"Treino: {str(status.get('trained_at', ''))[:19]} · {status.get('train_seconds')}s"
        )
    else:
        st.warning("Modelo ainda não treinado.")
        if st.button("🧠 Treinar IA agora"):
            with st.spinner("Treinando scikit-learn com o histórico local..."):
                try:
                    resp = requests.post(
                        f"{API_BASE}:{config.API_PORT}/ml/train?synthetic_bootstrap=true",
                        headers=_auth_headers(),
                        timeout=300,
                    )
                    if resp.status_code == 200:
                        st.success("IA treinada com sucesso!")
                        st.cache_data.clear()
                        fn = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
                        if fn:
                            fn()
                    else:
                        st.error(f"Falha no treino: HTTP {resp.status_code}")
                except Exception as e:
                    st.error(f"Erro ao treinar: {e}")


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_pattern_status() -> dict:
    """Metadados do padrão de movimento aprendido (não supervisionado)."""
    if not API_TOKEN:
        return {}
    try:
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/ml/pattern/status",
            headers=_auth_headers(),
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Erro ao buscar /ml/pattern/status: {e}")
    return {}


def _train_pattern(limit: int = 50000) -> None:
    with st.spinner("Observando o histórico e aprendendo o padrão (sem rótulos)..."):
        try:
            resp = requests.post(
                f"{API_BASE}:{config.API_PORT}/ml/pattern/train?limit={limit}",
                headers=_auth_headers(),
                timeout=300,
            )
            if resp.status_code == 200:
                st.success("Padrão da máquina aprendido com sucesso!")
                st.cache_data.clear()
                fn = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
                if fn:
                    fn()
            else:
                st.error(f"Falha no aprendizado: HTTP {resp.status_code} — {resp.text[:200]}")
        except Exception as e:
            st.error(f"Erro ao aprender padrão: {e}")


def render_pattern_card():
    """Card do padrão de movimento aprendido (IA sem rótulos)."""
    st.markdown("### 👁️ Padrão de Movimento (sem rótulos)")
    status = fetch_pattern_status()
    if not status:
        if not API_TOKEN:
            st.info("Configure DASHBOARD_API_TOKEN para ver/treinar o padrão da máquina.")
        return

    if status.get("trained"):
        th = status.get("thresholds", {})
        col1, col2 = st.columns(2)
        col1.metric("Janelas normais", status.get("n_core", 0))
        col2.metric("Regimes", status.get("n_regimes", 1))
        st.caption(
            f"Aprendeu observando {status.get('n_samples', 0)} amostras · "
            f"alerta z ≥ {th.get('rms_z_alert', '—')} · "
            f"crítico z ≥ {th.get('rms_z_critical', '—')}\n\n"
            f"Treino: {str(status.get('trained_at', ''))[:19]} · "
            f"{status.get('train_seconds')}s · **nenhum rótulo usado**"
        )
        regimes = status.get("regimes", [])
        if regimes:
            st.caption(
                "Padrões normais recorrentes: "
                + " · ".join(
                    f"R{r.get('id')} (pico {r.get('peak_median')})"
                    for r in regimes
                )
            )
        if st.button("👁️ Reaprender padrão agora"):
            _train_pattern()
    else:
        st.warning("A máquina ainda não teve o padrão aprendido.")
        st.caption(
            "A IA observa apenas os movimentos (sem status/rótulos) e descobre "
            "sozinha o comportamento normal."
        )
        if st.button("👁️ Aprender padrão agora"):
            _train_pattern()


def encode_image_to_base64(image_path: str) -> Optional[str]:
    """Codifica imagem para base64 (exibição em tabela)."""
    try:
        p = Path(image_path)
        if p.exists():
            with open(p, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            return f"data:image/jpeg;base64,{data}"
    except Exception as e:
        logger.error(f"Erro codificando imagem {image_path}: {e}")
    return None


def status_badge(status: str) -> str:
    """Retorna HTML/CSS do badge visual do status."""
    colors = {
        "NORMAL": "#28a745",       # Verde
        "ALERTA": "#ffc107",       # Amarelo
        "CRITICO": "#dc3545"       # Vermelho
    }
    color = colors.get(status, "#6c757d")
    return (
        f'<span style="background-color:{color}; '
        f'color:white;padding:4px 8px;border-radius:4px;font-size:0.85em;">{status}</span>'
    )


def status_indicator(status: str) -> str:
    """Retorna HTML do indicador de status pulsante para CRITICO."""
    if status == "CRITICO":
        # CSS para pulsação
        pulse = (
            "<style>"
            ".pulse { animation: pulse 1s infinite; }"
            "@keyframes pulse {"
            "  0% { opacity: 1; }"
            "  50% { opacity: 0.5; }"
            "  100% { opacity: 1; }"
            "}"
            "</style>"
        )
        return f"{pulse}<div style='width:20px;height:20px;background-color:#dc3545;border-radius:50%;display:inline-block;class=pulse;vertical-align:middle;'></div>"
    elif status == "ALERTA":
        return (
            f'<span style="background-color:#ffc107; '
            f'color:white;padding:4px 8px;border-radius:4px;font-size:0.85em;">{status}</span>'
        )
    else:
        return status_badge(status)


# ---------------------------------------------------------
# Componentes da Interface
# ---------------------------------------------------------


def render_header():
    """Renderiza cabeçalho da dashboard."""
    st.title("🏭 Dashboard Operacional - Manutenção Preditiva & Segurança Industrial")
    st.markdown(
        f"<div style='font-size:0.9em;color:gray;'>"
        f"Auto-refresh: a cada {REFRESH_SECONDS}s | "
        f"Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"
        f"</div>",
        unsafe_allow_html=True
    )


def fetch_snapshot() -> Optional[bytes]:
    """Busca o frame atual da câmera via API /snapshot."""
    try:
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/snapshot",
            timeout=3,
        )
        if resp.status_code == 200:
            return resp.content
        print(f"/snapshot retornou {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"Erro ao buscar snapshot: {e}")
    return None


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_temperature() -> dict:
    """Busca leitura térmica atual via API /temperature."""
    try:
        resp = requests.get(f"{API_BASE}:{config.API_PORT}/temperature", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Erro ao buscar /temperature: {e}")
    return {}


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_temperature_history(limit: int = 200) -> List[dict]:
    """Busca histórico térmico via API /temperature/history."""
    try:
        resp = requests.get(
            f"{API_BASE}:{config.API_PORT}/temperature/history",
            params={"limit": limit},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("points", [])
    except Exception as e:
        print(f"Erro ao buscar /temperature/history: {e}")
    return []


def render_temperature_panel():
    """Painel de telemetria térmica (câmera com sensor de temperatura)."""
    st.markdown("### 🌡️ Temperatura")
    data = fetch_temperature()
    hist = fetch_temperature_history(limit=150)
    temp = float(data.get("temperature", 0.0))
    thresholds = data.get("thresholds", {})
    alerta = float(thresholds.get("alerta", getattr(config, "TEMP_MAX_ALERTA", 60.0)))
    critico = float(thresholds.get("critico", getattr(config, "TEMP_MAX_CRITICO", 75.0)))

    if temp <= 0 and not hist:
        st.info("Sensor de temperatura não ativo. Ative TEMP_SENSOR_ENABLED=true e use TEMP_SOURCE=mock|file|sensor.")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Temperatura", f"{temp:.1f}°C")
    col2.metric("Limite ALERTA", f"{alerta:.1f}°C")
    col3.metric("Limite CRÍTICO", f"{critico:.1f}°C")

    # Gauge
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number+delta",
            value=temp,
            delta={"reference": alerta, "suffix": "°C"},
            gauge={
                "axis": {"range": [None, max(critico + 20, 100)]},
                "bar": {"color": "darkblue"},
                "steps": [
                    {"range": [0, alerta], "color": "#d4edda"},
                    {"range": [alerta, critico], "color": "#fff3cd"},
                    {"range": [critico, max(critico + 20, 100)], "color": "#f8d7da"},
                ],
                "threshold": {"line": {"color": "red", "width": 4}, "thickness": 0.75, "value": critico},
            },
            domain={"x": [0, 1], "y": [0, 1]},
            number={"suffix": "°C"},
        )
    )
    fig.update_layout(height=260, margin=dict(l=20, r=20, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

    # Histórico térmico
    if hist:
        df = pd.DataFrame(hist)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
        fig2 = go.Figure()
        fig2.add_trace(go.Scattergl(
            x=df["timestamp"], y=df["temperature"],
            mode="lines+markers", name="Temperatura (°C)",
            line=dict(color="#e74c3c", width=2),
        ))
        fig2.add_hline(y=alerta, line_dash="dash", line_color="orange", annotation_text="ALERTA")
        fig2.add_hline(y=critico, line_dash="dot", line_color="red", annotation_text="CRITICO")
        fig2.update_layout(
            title="Histórico térmico (últimos pontos)",
            xaxis_title="Tempo",
            yaxis_title="°C",
            template="plotly_white",
            height=250,
            margin=dict(l=60, r=20, t=40, b=40),
        )
        st.plotly_chart(fig2, use_container_width=True)


def render_live_camera():
    """Card com a câmera ao vivo (snapshot atualizado a cada refresh)."""
    st.markdown("### 📹 Câmera ao vivo")
    img = fetch_snapshot()
    if img:
        st.image(img, use_container_width=True)
        st.caption(f"Atualizado em {datetime.now():%H:%M:%S} UTC+0 · auto-refresh {REFRESH_SECONDS}s")
    else:
        st.info("Sem sinal da câmera — o Vision Monitor está rodando?")
    st.markdown(
        f"[🎥 Abrir stream MJPEG (site/APK)]({API_BASE}:{config.API_PORT}/stream)"
    )


def render_status_panel(status: str, magnitude: float):
    """Painel de status principal."""
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        st.markdown(f"### Status Atual")
        st.markdown(status_indicator(status), unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"### Magnitude")
        st.info(f"{magnitude:.3f}")
    
    with col3:
        st.markdown(f"### Histórico")
        # Contar últimos eventos
        pass


def render_telemetry_chart(points: List[dict]):
    """Renderiza gráfico de telemetria em tempo real."""
    if not points:
        st.info("Nenhum dado de telemetria disponível ainda.")
        return
    
    df = pd.DataFrame(points)
    # Inverter para ordem cronológica (mais antigo à esquerda)
    df = df.sort_values("timestamp")
    
    # Converter timestamps
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    
    # Criar figura
    fig = go.Figure()
    
    # Linha principal
    fig.add_trace(go.Scattergl(
        x=df["timestamp"],
        y=df["value"],
        mode="lines+markers",
        name="Magnitude Vibração",
        line=dict(color="#1f77b4", width=2),
        marker=dict(size=4, color="#ff7f0e"),
    ))
    
    # Linhas de threshold
    alert_threshold = config.ALERT_THRESHOLD if hasattr(config, 'ALERT_THRESHOLD') else 1.8
    critical_threshold = config.CRITICAL_THRESHOLD if hasattr(config, 'CRITICAL_THRESHOLD') else 3.5
    
    fig.add_hline(y=alert_threshold, line_dash="dash", line_color="orange",
                  annotation_text="ALERTA", annotation_position="right")
    fig.add_hline(y=critical_threshold, line_dash="dot", line_color="red",
                  annotation_text="CRITICO", annotation_position="right")
    
    # Layout
    fig.update_layout(
        title="Telemetria em Tempo Real - Índice de Vibração",
        xaxis_title="Tempo",
        yaxis_title="Magnitude",
        hovermode="x unified",
        template="plotly_white",
        height=400,
        margin=dict(l=60, r=20, t=40, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    
    st.plotly_chart(fig, use_container_width=True)


def render_recent_events(events: List[dict]):
    """Renderiza tabela de ocorrências recentes."""
    if not events:
        st.info("Nenhuma ocorrência registrada.")
        return
    
    # Limitar a 20
    events = events[:20]
    
    # Preparar dados
    rows = []
    for e in events:
        row = {
            "Timestamp": e.get("timestamp", ""),
            "Status": e.get("status", "NORMAL"),
            "Magnitude": f"{e.get('value', 0):.3f}",
            "Source": e.get("source", "vision"),
        }
        # Miniatura
        frame_path = e.get("frame_path", "")
        if frame_path:
            img_base64 = encode_image_to_base64(frame_path)
            if img_base64:
                row["Frame"] = f'<img src="{img_base64}" width="50" height="30">'
            else:
                row["Frame"] = "❌"
        else:
            row["Frame"] = "—"
        rows.append(row)
    
    df = pd.DataFrame(rows)
    
    # Exibir com formatação
    st.dataframe(
        df,
        column_config={
            "Timestamp": st.column_config.DatetimeColumn("Timestamp", format="HH:mm:ss"),
            "Status": st.column_config.TextColumn("Status"),
            "Magnitude": st.column_config.NumberColumn("Magnitude"),
            "Frame": st.column_config.ImageColumn("Frame", help="Frame capturado"),
        },
        hide_index=True,
        use_container_width=True,
        height=300
    )


def render_controls():
    """Renderiza controles da interface."""
    st.sidebar.header("🎛️ Controles")
    
    # Auto-refresh
    auto_refresh = st.sidebar.checkbox(
        "Auto-refresh automático",
        value=True,
        help="Atualiza a cada alguns segundos"
    )
    
    # Refresh manual
    if st.sidebar.button("🔄 Atualizar Agora"):
        st.rerun()
    
    # Controles de E-Stop
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Segurança Industrial")
    
    if st.sidebar.button("🚨 PARADA DE EMERGÊNCIA MANUAL", type="primary", use_container_width=True):
        try:
            resp = requests.post(
                f"{API_BASE}:{config.API_PORT}/manual-stop",
                json={"user_id": "dashboard_operator", "reason": "Dashboard manual stop"},
                timeout=5
            )
            if resp.status_code == 200:
                st.sidebar.success("Comando enviado ao ESP32")
            else:
                st.sidebar.error(f"Erro: {resp.text}")
        except Exception as e:
            st.sidebar.error(f"Falha ao conectar: {e}")
    
    if st.sidebar.button("⚙️ RESET DO SISTEMA", type="secondary", use_container_width=True):
        try:
            resp = requests.post(
                f"{API_BASE}:{config.API_PORT}/reset",
                json={"user_id": "dashboard_operator", "reason": "Dashboard reset"},
                timeout=5
            )
            if resp.status_code == 200:
                st.sidebar.success("Reset enviado ao ESP32")
            else:
                st.sidebar.error(f"Erro: {resp.text}")
        except Exception as e:
            st.sidebar.error(f"Falha ao conectar: {e}")
    
    # Exportação CSV
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Exportação de Dados")
    
    start_date = st.sidebar.date_input("Data inicial", value=None)
    end_date = st.sidebar.date_input("Data final", value=None)
    
    if st.sidebar.button("📥 Exportar CSV", use_container_width=True):
        with st.spinner("Exportando CSV..."):
            csv_content = export_csv_cached(
                start_date=datetime.combine(start_date, datetime.min.time()) if start_date else None,
                end_date=datetime.combine(end_date, datetime.max.time()) if end_date else None,
                status_filter=st.sidebar.selectbox("Filtrar por status", ["Todos", "NORMAL", "ALERTA", "CRITICO"])
            )
        
        if csv_content:
            st.sidebar.download_button(
                label="⬇️ Baixar CSV",
                data=csv_content,
                file_name=f"events_{datetime.utcnow():%Y%m%d}.csv",
                mime="text/csv"
            )


def render_sidebar_info():
    """Painel lateral com informações rápidas."""
    with st.sidebar.expander("ℹ️ Informações do Sistema", expanded=False):
        st.write(f"**API Base:** {API_BASE}:{config.API_PORT}")
        st.write(f"**Fonte de Vídeo:** {config.VIDEO_SOURCE}")
        st.write(f"**Threshold Alert:** {config.ALERT_THRESHOLD}")
        st.write(f"**Threshold Critical:** {config.CRITICAL_THRESHOLD}")
        st.write(f"**Thresholds ROIs:** {config.ROI_COORDS}")


# ---------------------------------------------------------
# Loop Principal / Auto-refresh
# ---------------------------------------------------------


def main_loop():
    """Loop principal da dashboard."""
    init_session_state()
    render_header()
    
    # Lógica de auto-refresh via session state e timer
    # Streamlit auto-runs on script reread; use experimental_rerun
    
    # Buscar dados atuais
    status_data = fetch_status_cached()
    events = fetch_events_cached(limit=50)
    telemetry = fetch_telemetry_cached(limit=MAX_TELEMETRY_POINTS)
    
    # Extrair estado e magnitude
    current_status = status_data.get("current_status", "NORMAL")
    last_magnitude = status_data.get("last_value", 0.0)
    
    # Atualizar estado da sessão
    st.session_state.last_status = current_status
    st.session_state.last_magnitude = last_magnitude
    
    # Renderizar componentes
    render_status_panel(current_status, last_magnitude)
    
    # Painel lateral
    with st.sidebar:
        render_sidebar_info()
        render_controls()
    
    # Área central: Telemetria e Eventos
    col_left, col_right = st.columns([2, 1])
    
    with col_left:
        render_live_camera()
        render_telemetry_chart(telemetry)
        render_temperature_panel()
    
    with col_right:
        st.markdown("### 📋 Últimas Ocorrências")
        render_recent_events(events)
        render_ai_diagnosis()
        render_ml_card()
        render_pattern_card()
    
    # Verificar se precisa resetar estado baseado em E-Stop
    if current_status == "CRITICO":
        st.error("⚠️ Estado CRÍTICO detectado! Verificar máquina imediatamente.")
    elif current_status == "ALERTA":
        st.warning("⚠️ Estado ALERTA! Verificar máquina.")
    else:
        st.success("✅ Sistema em operação normal.")


# ---------------------------------------------------------
# Entrada Principal
# ---------------------------------------------------------


def main():
    """Função principal da aplicação Streamlit."""
    main_loop()


if __name__ == "__main__":
    main()