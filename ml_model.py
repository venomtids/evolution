"""
ml_model.py
IA simples de código aberto treinada para a nossa situação.

Stack 100% open-source (licenças BSD):
  - scikit-learn : IsolationForest (detecção de anomalias não-supervisionada)
                   RandomForestClassifier (classificação NORMAL/ALERTA/CRITICO)
  - joblib       : persistência dos modelos
  - NumPy        : features

Contexto industrial:
  O índice de vibração é o percentil 99 da magnitude do fluxo óptico Farnebäck
  (ver vision_monitor.py). Uma janela das últimas N amostras caracteriza o
  comportamento mecânico recente da máquina:

  Features por janela:
    mean, std, min, max, p90, p95, p99, range, iqr,
    delta_mean (média das últimas 10 − média das anteriores),
    alert_rate, critico_rate (fração de ALERTA / CRITICO na janela)

Funcionamento:
  1. python train_ai.py                                  # treina a partir do SQLite local
  2. POST /ml/train  (ou botão no dashboard)             # treina via API (JWT)
  3. AI_PROVIDER=ml no .env                              # usa esta IA nos laudos
     (sem modelo treinado → fallback automático p/ heurística ISO-10816 local)

Bootstrap sintético:
  Como uma instalação nova tem pouco histórico, o treino pode gerar dados
  sintéticos calibrados para a dinâmica do mock/webcam (NORMAL/Alerta/Crítico).
  Desativável com --synthetic-only=False ou pelo parâmetro do endpoint.
"""

import json
import logging
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import joblib

from sklearn.ensemble import IsolationForest, RandomForestClassifier

logger = logging.getLogger("ml_model")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

WINDOW = 60                      # amostras por janela (~15 s a 4 Hz)
STRIDE = 10                      # passo de deslize da janela nos dados de treino
MODEL_DIR = Path("models")
IF_PATH = MODEL_DIR / "anomaly_iforest.joblib"
RF_PATH = MODEL_DIR / "status_classifier.joblib"
META_PATH = MODEL_DIR / "metadata.json"

FEATURE_NAMES = [
    "mean", "std", "min", "max",
    "p90", "p95", "p99", "range", "iqr",
    "delta_mean", "alert_rate", "critico_rate",
]


# ---------------------------------------------------------------------------
# Extração de features
# ---------------------------------------------------------------------------

def extract_features(values: List[float],
                     statuses: Optional[List[str]] = None) -> np.ndarray:
    """Vetor de features (1D) de uma janela de valores de magnitude."""
    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        v = np.zeros(1)

    if statuses:
        s = list(statuses)[-v.size:] if len(statuses) >= v.size else ["NORMAL"] * v.size
    else:
        s = ["NORMAL"] * v.size

    alert_rate = s.count("ALERTA") / len(s)
    critico_rate = s.count("CRITICO") / len(s)

    tail = v[-10:]
    head = v[:-10]
    delta_mean = float(tail.mean() - (head.mean() if head.size else tail.mean()))

    q1, q3 = np.percentile(v, [25, 75])
    feats = [
        float(np.mean(v)),
        float(np.std(v)),
        float(np.min(v)),
        float(np.max(v)),
        float(np.percentile(v, 90)),
        float(np.percentile(v, 95)),
        float(np.percentile(v, 99)),
        float(np.max(v) - np.min(v)),
        float(q3 - q1),
        delta_mean,
        alert_rate,
        critico_rate,
    ]
    return np.array(feats, dtype=np.float64)


def _windows(values: List[float],
             statuses: List[str],
             window: int,
             stride: int) -> Tuple[np.ndarray, np.ndarray]:
    """Desliza janelas sobre o histórico → (X, y_status_dominante)."""
    xs, ys = [], []
    n = len(values)
    for start in range(0, n - window + 1, stride):
        chunk_v = values[start:start + window]
        chunk_s = statuses[start:start + window]
        xs.append(extract_features(chunk_v, chunk_s))
        critico = chunk_s.count("CRITICO")
        alerta = chunk_s.count("ALERTA")
        if critico > alerta and critico > 0:
            ys.append("CRITICO")
        elif alerta > 0:
            ys.append("ALERTA")
        else:
            ys.append("NORMAL")
    if not xs:
        return np.empty((0, len(FEATURE_NAMES))), np.array([], dtype=object)
    return np.vstack(xs), np.array(ys, dtype=object)


# ---------------------------------------------------------------------------
# Geração de dados sintéticos (bootstrap para instalações novas)
# ---------------------------------------------------------------------------

def synthetic_bootstrap(n_per_class: int = 400,
                        seed: int = 42) -> Tuple[List[float], List[str]]:
    """
    Gera sequências sintéticas calibradas para a dinâmica esperada de
    máquinas rotativas (e do mock do vision_monitor):
      NORMAL  ~ baixo ruído estável   (média ~0.36)
      ALERTA  ~ oscilação elevada     (média ~2.6)
      CRITICO ~ vibração muito alta   (média ~6.5, com picos)
    Retorna (values, statuses) concatenados com transições raras.
    """
    rng = np.random.default_rng(seed)
    values: List[float] = []
    statuses: List[str] = []

    for _ in range(n_per_class):
        length = int(rng.integers(40, 90))
        base = np.abs(rng.normal(0.35, 0.12, length))
        values.extend(base.tolist())
        statuses.extend(["NORMAL"] * length)

    for _ in range(n_per_class):
        length = int(rng.integers(40, 90))
        base = np.abs(rng.normal(2.6, 0.7, length)) + 0.4 * np.sin(np.linspace(0, 6, length))
        values.extend(base.clip(0.5).tolist())
        statuses.extend(["ALERTA"] * length)

    for _ in range(n_per_class):
        length = int(rng.integers(40, 90))
        base = np.abs(rng.normal(6.5, 1.8, length))
        spikes = rng.random(length) < 0.15
        base = base + spikes * rng.uniform(2.0, 6.0, length)
        values.extend(base.tolist())
        statuses.extend(["CRITICO"] * length)

    return values, statuses


# ---------------------------------------------------------------------------
# Motor de ML
# ---------------------------------------------------------------------------

class MLEngine:
    """Treina, persiste e aplica os modelos open-source."""

    def __init__(self, model_dir: Path = MODEL_DIR):
        self.model_dir = Path(model_dir)
        self.if_path = self.model_dir / IF_PATH.name
        self.rf_path = self.model_dir / RF_PATH.name
        self.meta_path = self.model_dir / META_PATH.name
        self.window = WINDOW
        self._iforest: Optional[IsolationForest] = None
        self._rf: Optional[RandomForestClassifier] = None
        self._meta: Dict[str, Any] = {}
        self._load()

    # ----- persistência -----
    def _load(self):
        try:
            if self.meta_path.exists():
                self._meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if self.if_path.exists():
                self._iforest = joblib.load(self.if_path)
            if self.rf_path.exists():
                self._rf = joblib.load(self.rf_path)
        except Exception as e:
            logger.warning(f"Falha ao carregar modelos ML: {e}")
            self._iforest = None
            self._rf = None
            self._meta = {}

    # ----- treino -----
    def train(self,
              events: List[Dict[str, Any]],
              synthetic_bootstrap: bool = True) -> Dict[str, Any]:
        """
        Treina com o histórico (lista de dicts com 'value' e 'status').
        Data minimamente necessária: algumas dezenas de janelas; caso contrário
        completa com bootstrap sintético se synthetic_bootstrap=True.
        """
        t0 = time.time()
        values = [float(e.get("value", 0) or 0) for e in events]
        statuses = [e.get("status", "NORMAL") for e in events]
        n_real_windows = max(0, (len(values) - self.window) // STRIDE + 1)

        used_synthetic = False
        synth_meta = {}
        if synthetic_bootstrap and n_real_windows < 50:
            s_values, s_statuses = synthetic_bootstrap(n_per_class=400)
            values = values + s_values
            statuses = statuses + s_statuses
            used_synthetic = True
            synth_meta = {"synthetic_sequences": 1200, "synthetic_samples": len(s_values)}

        X, y = _windows(values, statuses, self.window, STRIDE)
        if X.shape[0] < 10:
            return {"ok": False, "reason": "dados insuficientes (menos de 10 janelas)",
                    "n_windows": int(X.shape[0])}

        # 1) IsolationForest: treina no comportamento NORMAL (e perto dele)
        normal_mask = (y == "NORMAL")
        X_normal = X[normal_mask] if normal_mask.any() else X
        iforest = IsolationForest(
            n_estimators=200,
            contamination=0.05,
            random_state=42,
            n_jobs=-1,
        )
        iforest.fit(X_normal)
        train_scores = iforest.decision_function(X)
        score_p1, score_p50, score_p99 = np.percentile(train_scores, [1, 50, 99])

        # 2) RandomForest supervisionado — só se há classes suficientes
        rf_metrics: Dict[str, Any] = {}
        rf_model = None
        classes, counts = np.unique(y, return_counts=True)
        if len(classes) >= 2 and counts.min() >= 20:
            rf_model = RandomForestClassifier(
                n_estimators=200,
                max_depth=10,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1,
            )
            rf_model.fit(X, y)
            train_acc = float(np.mean(rf_model.predict(X) == y))
            rf_metrics = {
                "classes": [str(c) for c in classes],
                "class_counts": dict(zip([str(c) for c in classes], [int(c) for c in counts])),
                "train_accuracy": round(train_acc, 4),
            }

        # 3) Persiste
        self.model_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(iforest, self.if_path)
        self._iforest = iforest
        if rf_model is not None:
            joblib.dump(rf_model, self.rf_path)
            self._rf = rf_model
        else:
            self._rf = None
            if self.rf_path.exists():
                self.rf_path.unlink()

        self._meta = {
            "trained": True,
            "trained_at": datetime.utcnow().isoformat(),
            "train_seconds": round(time.time() - t0, 2),
            "window": self.window,
            "stride": STRIDE,
            "feature_names": FEATURE_NAMES,
            "n_samples": int(len(values)),
            "n_real_events": int(len(events)),
            "used_synthetic_bootstrap": used_synthetic,
            "synthetic": synth_meta,
            "n_windows": int(X.shape[0]),
            "n_normal_windows": int(normal_mask.sum()),
            "score_p1": float(score_p1),
            "score_p50": float(score_p50),
            "score_p99": float(score_p99),
            "iso_forest": {"n_estimators": 200, "contamination": 0.05},
            "classifier": rf_metrics or {"classes": [], "train_accuracy": None},
        }
        self.meta_path.write_text(json.dumps(self._meta, indent=2, default=str),
                                  encoding="utf-8")
        logger.info(f"ML treinado: {self._meta['n_windows']} janelas "
                    f"({self._meta['n_normal_windows']} normais) em {self._meta['train_seconds']}s")
        return {"ok": True, **self._meta}

    # ----- inferência -----
    def predict(self,
                values: List[float],
                statuses: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Analisa a janela recente. Retorna laudo estruturado (ou trained=False
        se o modelo ainda não foi treinado).
        """
        if self._iforest is None:
            return {"trained": False,
                    "reason": "modelo não treinado (rode train_ai.py ou POST /ml/train)"}

        v = list(values)[-self.window:]
        s = (list(statuses)[-len(v):] if statuses else None)
        x = extract_features(v, s).reshape(1, -1)

        score = float(self._iforest.decision_function(x)[0])   # maior = mais normal
        is_anomaly = bool(self._iforest.predict(x)[0] == -1)

        # Normaliza score → severidade_alinhada com thresholds do projeto
        peak = float(np.max(v)) if v else 0.0
        p1 = self._meta.get("score_p1", -0.2)
        p50 = self._meta.get("score_p50", 0.0)
        # quão anormal (0 = típico, 1 = muito anormal)
        denom = max(abs(p50 - p1), 1e-6)
        anomaly = float(np.clip((p50 - score) / denom, 0.0, 1.0)) if score < p50 else 0.0

        # Classifier (se treinado)
        classifier_pred = None
        classifier_proba = None
        if self._rf is not None:
            classifier_pred = str(self._rf.predict(x)[0])
            proba = self._rf.predict_proba(x)[0]
            classifier_proba = {cls: round(float(p), 3)
                                for cls, p in zip(self._rf.classes_, proba)}

        # Decisão final combinando detector + classificador + pico absoluto
        if classifier_pred == "CRITICO" or (is_anomaly and peak >= 3.5):
            severidade = "critica"
        elif classifier_pred == "ALERTA" or is_anomaly:
            severidade = "alta" if peak >= 1.8 else "media"
        else:
            severidade = "baixa"

        confidence = round(0.5 + 0.45 * anomaly, 2) if severidade != "baixa" else 0.85

        recom = {
            "critica": "Pare a máquina imediatamente (E-Stop). Inspecione rolamentos, acoplamento e fixação. Não religar sem manutenção.",
            "alta": "Reduza carga, agende inspeção em até 24h e verifique alinhamento/eixos.",
            "media": "Monitorar com atenção; agendar manutenção preventiva nos próximos 7 dias.",
            "baixa": "Comportamento dentro do baseline estabelecido.",
        }[severidade]

        causa = (
            f"Padrão de vibração anômalo detectado por IA aberta (IsolationForest). "
            f"Score de anomalia={anomaly:.2f}, pico={peak:.3f}"
            + (f", classificador RF→{classifier_pred}" if classifier_pred else "")
        )

        return {
            "trained": True,
            "severidade": severidade,
            "diagnostico": (f"IsolationForest marcou a janela como "
                            f"{'ANÔMALA' if is_anomaly else 'normal'} "
                            f"(score {score:.4f}, anomalia {anomaly:.2f})."),
            "causa_provavel": causa,
            "recomendacao": recom,
            "confidence": confidence,
            "provider": "ml",
            "ml": {
                "is_anomaly": is_anomaly,
                "decision_function": round(score, 4),
                "anomaly_index": round(anomaly, 4),
                "peak_magnitude": round(peak, 4),
                "classifier_prediction": classifier_pred,
                "classifier_proba": classifier_proba,
                "trained_at": self._meta.get("trained_at"),
            },
        }

    # ----- status -----
    def status(self) -> Dict[str, Any]:
        if not self._meta.get("trained"):
            return {"trained": False,
                    "reason": "modelo não treinado (rode train_ai.py ou POST /ml/train)",
                    "model_dir": str(self.model_dir)}
        return dict(self._meta)


# Singleton usado pela API e pelo motor de diagnóstico
ml_engine = MLEngine()
