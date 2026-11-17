"""
diagnostic_ai.py
Motor de diagnóstico por IA para manutenção preditiva.

Providers suportados (configuráveis via .env):
  - ml         : IA aberta treinada (scikit-learn: IsolationForest + RandomForest)
  - gemini     : Google Gemini API (free tier generoso: Gemini 2.0 Flash)
  - openrouter : OpenRouter (modelos gratuitos, ex: deepseek/deepseek-r1:free)
  - local      : Heurística local determinística (ILIMITADO, sem rede, fallback)

Funcionalidades:
  - Analisa janela de eventos de telemetria (status, magnitude, timestamps).
  - Retorna laudo estruturado: diagnóstico, causa provável, severidade,
    recomendação, confiança.
  - ErrorTracker: conta erros em janela deslizante e dispara aviso quando
    a máquina excede o limite (burst de erros).
  - Cooldown entre chamadas de IA para evitar spam/custos.

Uso:
    engine = DiagnosticEngine()
    laudo = engine.analyze(events, status_snapshot="CRITICO")
"""

import json
import re
import time
import logging
import threading
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import requests

from config import config

logger = logging.getLogger("diagnostic_ai")

# ===========================================================================
# Esquema de saída do laudo
# ===========================================================================

DIAGNOSIS_SCHEMA = {
    "diagnostico": str,        # texto livre explicando o problema
    "causa_provavel": str,     # causa raiz mais provável
    "severidade": str,         # baixa | media | alta | critica
    "recomendacao": str,       # ação recomendada
    "confidence": float,       # 0.0 - 1.0
    "provider": str,           # gemini | openrouter | local
}


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


# ===========================================================================
# Rastreador de erros em janela deslizante
# ===========================================================================

class ErrorTracker:
    """
    Conta eventos ALERTA/CRÍTICO dentro de uma janela de tempo deslizante.
    Quando a quantidade ultrapassa ERROR_BURST_LIMIT, dispara um aviso
    ("máquina cometendo muitos erros") com cooldown para evitar repetição.
    """

    def __init__(self, window_seconds: int = None, burst_limit: int = None,
                 alert_cooldown: int = None):
        self.window = window_seconds or config.ERROR_WINDOW_SECONDS
        self.burst_limit = burst_limit or config.ERROR_BURST_LIMIT
        self.alert_cooldown = alert_cooldown or config.ERROR_BURST_COOLDOWN
        self._events: deque = deque()  # timestamps de eventos ALERTA/CRITICO
        self._last_burst_alert: Optional[datetime] = None
        self._lock = threading.Lock()

    def add(self, status: str, timestamp: Optional[datetime] = None) -> bool:
        """
        Registra um evento. Retorna True se configura BURST de erros
        (ou seja, máquina 'cometendo muitos erros' agora).
        """
        ts = timestamp or datetime.utcnow()
        with self._lock:
            if status in ("ALERTA", "CRITICO"):
                self._events.append(ts)
            self._purge(ts)
            count = len(self._events)
            if count >= self.burst_limit:
                if self._can_alert(ts):
                    self._last_burst_alert = ts
                    return True
        return False

    def _purge(self, now: datetime):
        cutoff = now - timedelta(seconds=self.window)
        while self._events and self._events[0] < cutoff:
            self._events.popleft()

    def _can_alert(self, now: datetime) -> bool:
        if self._last_burst_alert is None:
            return True
        return (now - self._last_burst_alert).total_seconds() >= self.alert_cooldown

    def count(self) -> int:
        with self._lock:
            self._purge(datetime.utcnow())
            return len(self._events)

    def reset(self):
        with self._lock:
            self._events.clear()


# ===========================================================================
# Construtor do prompt
# ===========================================================================

def build_prompt(events: List[Dict[str, Any]], stats: Dict[str, Any]) -> str:
    """Monta o prompt com contexto industrial + dados da janela."""
    lines = [
        "Você é um engenheiro sênior de manutenção preditiva industrial.",
        "Analise os dados de vibração/movimento abaixo de uma máquina industrial",
        "monitorada por visão computacional (Dense Optical Flow Farnebäck).",
        "",
        "Responda APENAS com um JSON válido (sem markdown, sem explicação extra)",
        "no seguinte formato exato:",
        '{',
        '  "diagnostico": "texto curto do que está acontecendo",',
        '  "causa_provavel": "causa raiz mais provável (ex: desbalanceamento,',
        '                       rolamento degradado, folga mecânica, acoplamento)",',
        '  "severidade": "baixa|media|alta|critica",',
        '  "recomendacao": "ação imediata recomendada para o operador",',
        '  "confidence": 0.0',
        '}',
        "",
        "DADOS DA TELEMETRIA:",
        f"- Janela analisada: {stats.get('window_seconds')}s",
        f"- Total de eventos: {stats.get('total_events')}",
        f"- NORMAL: {stats.get('normal')} | ALERTA: {stats.get('alerta')} | CRITICO: {stats.get('critico')}",
        f"- Magnitude média: {stats.get('avg_magnitude'):.3f}",
        f"- Magnitude de pico: {stats.get('peak_magnitude'):.3f}",
        f"- Taxa de erros (eventos não-NORMAL por minuto): {stats.get('error_rate'):.2f}",
        f"- Último status: {stats.get('last_status')}",
        "",
        "ÚLTIMOS EVENTOS (timestamp, status, magnitude):",
    ]
    for e in events[-20:]:  # limita contexto
        lines.append(f"  - {e.get('timestamp')} | {e.get('status')} | {e.get('value'):.3f}")
    lines.append("")
    lines.append("JSON de resposta:")
    return "\n".join(lines)


def parse_ai_json(text: str) -> Dict[str, Any]:
    """Extrai um JSON do texto de resposta da IA (tolera markdown e texto solto)."""
    if not text:
        return {}
    # Tenta encontrar bloco entre chaves
    text = text.strip()
    # Remove markdown ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        # Procura primeiro { ... } equilibrado
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Tentativa de reparo: chaves/quotes simples
        try:
            fixed = re.sub(r"(\w+):", r'"\1":', text)
            fixed = fixed.replace("'", '"')
            return json.loads(fixed)
        except json.JSONDecodeError:
            return {}


# ===========================================================================
# Providers
# ===========================================================================

class AIProvider(ABC):
    name = "abstract"

    @abstractmethod
    def diagnose(self, events: List[Dict[str, Any]], stats: Dict[str, Any]) -> Dict[str, Any]:
        """Retorna laudo no formato DIAGNOSIS_SCHEMA."""


class GeminiProvider(AIProvider):
    """Google Gemini API (free tier). Requer GEMINI_API_KEY."""

    name = "gemini"

    def __init__(self):
        self.api_key = config.GEMINI_API_KEY
        self.model = config.AI_MODEL or "gemini-2.0-flash"
        self.url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )

    def diagnose(self, events, stats) -> Dict[str, Any]:
        prompt = build_prompt(events, stats)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 512},
        }
        r = requests.post(self.url, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = parse_ai_json(text)
        parsed["provider"] = self.name
        return parsed


class OpenRouterProvider(AIProvider):
    """OpenRouter (modelos gratuitos). Requer OPENROUTER_API_KEY."""

    name = "openrouter"

    def __init__(self):
        self.api_key = config.OPENROUTER_API_KEY
        self.model = config.AI_MODEL or "deepseek/deepseek-r1:free"
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    def diagnose(self, events, stats) -> Dict[str, Any]:
        prompt = build_prompt(events, stats)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system",
                 "content": "Você é engenheiro de manutenção preditiva. Responda APENAS JSON válido."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        r = requests.post(self.url, json=payload, headers=headers, timeout=45)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
        parsed = parse_ai_json(text)
        parsed["provider"] = self.name
        return parsed


class LocalHeuristicProvider(AIProvider):
    """
    Fallback determinístico ILIMITADO: usa regras de engenharia clássicas
    (ISO 10816 / limites de vibração) para emitir laudo sem rede/API.
    Sempre disponível, custo zero.
    """

    name = "local"

    def diagnose(self, events, stats) -> Dict[str, Any]:
        critico = stats.get("critico", 0)
        alerta = stats.get("alerta", 0)
        peak = stats.get("peak_magnitude", 0.0)
        rate = stats.get("error_rate", 0.0)

        # Regras
        if critico >= 3 or peak >= 6.0:
            sev = "critica"
            causa = "Vibração excessiva recorrente — provável falha de rolamento ou desbalanceamento severo"
            rec = "Pare a máquina imediatamente (E-Stop). Inspecione rolamentos, acoplamento e fixação. Não religar sem manutenção."
            conf = 0.75
        elif critico >= 1 or rate > 5.0:
            sev = "alta"
            causa = "Pico de vibração acima do limite crítico — possível folga mecânica ou desgaste avançado"
            rec = "Reduza carga, agende inspeção em até 24h e verifique alinhamento/eixos."
            conf = 0.65
        elif alerta >= 5 or rate > 1.0:
            sev = "media"
            causa = "Oscilações recorrentes acima do baseline — início de degradação (desbalanceamento/Desalinhamento)"
            rec = "Monitorar com atenção; agendar manutenção preventiva nos próximos 7 dias."
            conf = 0.55
        else:
            sev = "baixa"
            causa = "Comportamento dentro do baseline estabelecido na calibração"
            rec = "Continuar monitoramento normal."
            conf = 0.9

        return {
            "diagnostico": f"Janela de {stats.get('window_seconds')}s: {critico} crítico(s), {alerta} alerta(s), pico {peak:.2f}.",
            "causa_provavel": causa,
            "severidade": sev,
            "recomendacao": rec,
            "confidence": conf,
            "provider": self.name,
        }


class MLProvider(AIProvider):
    """
    IA aberta treinada para a nossa situação (scikit-learn, ver ml_model.py).
    IsolationForest (anomalias não-supervisionadas) + RandomForest (rótulos
    NORMAL/ALERTA/CRITICO). Requer python train_ai.py ou POST /ml/train.
    Se o modelo não existir, levanta exceção → fallback ISO-10816.
    """

    name = "ml"

    def diagnose(self, events, stats) -> Dict[str, Any]:
        from ml_model import ml_engine
        values = [float(e.get("value", 0) or 0) for e in events]
        statuses = [e.get("status", "NORMAL") for e in events]
        result = ml_engine.predict(values, statuses)
        if not result.get("trained"):
            raise RuntimeError(result.get("reason", "modelo não treinado"))
        result["provider"] = self.name
        return result


# ===========================================================================
# Motor principal
# ===========================================================================

class DiagnosticEngine:
    """
    Orquestra análise diagnóstica:
      - Escolhe provider conforme .env (gemini/openrouter; fallback local).
      - Aplica cooldown entre análises.
      - Persiste laudos via callback (injetado pela API).
    """

    def __init__(self, persist_callback=None):
        self.persist_callback = persist_callback  # callable(diagnosis: dict)
        self.cooldown = config.AI_COOLDOWN_SECONDS
        self._last_run: Optional[datetime] = None
        self._lock = threading.Lock()
        self.error_tracker = ErrorTracker()
        self._provider: Optional[AIProvider] = None

    # ----- provider -----
    def _get_provider(self) -> AIProvider:
        if self._provider is not None:
            return self._provider
        provider_name = (config.AI_PROVIDER or "local").lower()
        if provider_name == "ml":
            self._provider = MLProvider()
        elif provider_name == "gemini" and config.GEMINI_API_KEY:
            self._provider = GeminiProvider()
        elif provider_name == "openrouter" and config.OPENROUTER_API_KEY:
            self._provider = OpenRouterProvider()
        else:
            if provider_name != "local":
                logger.warning(
                    f"Provider '{provider_name}' não configurado (API key ausente?). "
                    "Usando heurística LOCAL ilimitada."
                )
            self._provider = LocalHeuristicProvider()
        return self._provider

    def set_provider(self, name: str):
        """Força troca de provider em runtime (ex: teste)."""
        self._provider = None
        if name == "ml":
            self._provider = MLProvider()
        elif name == "gemini":
            self._provider = GeminiProvider()
        elif name == "openrouter":
            self._provider = OpenRouterProvider()
        else:
            self._provider = LocalHeuristicProvider()

    # ----- análise -----
    def analyze(self, events: List[Dict[str, Any]],
                status_snapshot: str,
                window_seconds: int = None) -> Dict[str, Any]:
        """
        Analisa janela de eventos e retorna laudo. Aplica cooldown.
        Persiste via callback se configurado.
        """
        window = window_seconds or config.ERROR_WINDOW_SECONDS
        now = datetime.utcnow()
        with self._lock:
            if self._last_run and (now - self._last_run).total_seconds() < self.cooldown:
                logger.debug("Cooldown de IA ativo; análise ignorada.")
                return {"skipped": True, "reason": "cooldown"}

            # Estatísticas da janela
            cutoff = now - timedelta(seconds=window)
            in_window = [
                e for e in events
                if datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00").replace("+00:00", ""))
                >= cutoff
            ] if events else []

            values = [float(e.get("value", 0) or 0) for e in in_window]
            counts = {"NORMAL": 0, "ALERTA": 0, "CRITICO": 0}
            for e in in_window:
                s = e.get("status", "NORMAL")
                counts[s] = counts.get(s, 0) + 1

            minutes = max(window / 60.0, 1 / 60.0)
            non_normal = counts.get("ALERTA", 0) + counts.get("CRITICO", 0)
            stats = {
                "window_seconds": window,
                "total_events": len(in_window),
                "normal": counts.get("NORMAL", 0),
                "alerta": counts.get("ALERTA", 0),
                "critico": counts.get("CRITICO", 0),
                "avg_magnitude": sum(values) / len(values) if values else 0.0,
                "peak_magnitude": max(values) if values else 0.0,
                "error_rate": non_normal / minutes,
                "last_status": status_snapshot,
            }

            try:
                provider = self._get_provider()
                diagnosis = provider.diagnose(in_window, stats)
            except Exception as e:
                logger.error(f"Falha do provider de IA: {e}. Usando heurística local.")
                diagnosis = LocalHeuristicProvider().diagnose(in_window, stats)

            # Normaliza campos obrigatórios
            diagnosis.setdefault("diagnostico", "(sem diagnóstico)")
            diagnosis.setdefault("causa_provavel", "(indisponível)")
            diagnosis.setdefault("severidade", "media")
            diagnosis.setdefault("recomendacao", "(sem recomendação)")
            diagnosis.setdefault("confidence", 0.0)
            diagnosis.setdefault("provider", "local")
            diagnosis["timestamp"] = _now_iso()
            diagnosis["status_snapshot"] = status_snapshot
            diagnosis["stats"] = stats

            self._last_run = now

        if self.persist_callback:
            try:
                self.persist_callback(diagnosis)
            except Exception as e:
                logger.error(f"Falha ao persistir diagnóstico: {e}")

        return diagnosis

    # ----- burst de erros -----
    def track_event(self, status: str, timestamp: Optional[datetime] = None) -> bool:
        """Registra evento no ErrorTracker; True se burst de erros detectado."""
        return self.error_tracker.add(status, timestamp)


# Instância global (singleton) — a API injeta o callback de persistência
diagnostic_engine = DiagnosticEngine()
