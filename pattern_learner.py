"""
pattern_learner.py
Aprendizado NÃO SUPERVISIONADO do padrão de movimento da máquina.

A IA não recebe rótulos, thresholds, diagnósticos nem qualquer informação
prévia sobre a máquina. Ela apenas observa a série temporal do índice de
movimento (percentil 99 do fluxo óptico Farnebäck) e descobre sozinha:

  1. Quais trechos formam o comportamento PADRÃO (regiões densas no espaço
     de features) — via distância k-NN + recorte robusto (median/MAD).
  2. Quantos REGIMES de operação normal existem (ex.: parada, vazio, carga
     nominal) — via KMeans sobre as janelas consideradas normais.
  3. Os limites estatísticos do normal — percentis das distâncias k-NN das
     próprias janelas normais (nenhum threshold humano é usado).
  4. Um modelo IsolationForest (não supervisionado) para reforçar a detecção
     de padrões raros.

Features por janela (WINDOW amostras, amostradas a PATTERN_SAMPLE_HZ):
  amplitude  : mean, std, min, max, p10, p50, p90, p99, iqr, range, cv
  dinâmica   : delta, slope, ac1 (autocorrelação lag-1), sign_changes
  ritmo      : spec_entropy, dom_freq, low_band, mid_band, high_band
  forma      : skew, kurtosis

Uso:
    python learn_pattern.py                          # aprende do SQLite (sem rótulos)
    python learn_pattern.py --source mock --seconds 30
    python learn_pattern.py --source 0 --seconds 60   # webcam ao vivo
    python learn_pattern.py --reset

Integrações:
    - vision_monitor.py aprende automaticamente na inicialização e pontua
      cada janela em tempo real (padrão normal vs. desvio).
    - POST /ml/pattern/train  (API, JWT) treina a partir do histórico.
    - POST /ml/pattern/reset  (API, JWT) apaga o padrão aprendido.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.cluster import KMeans
from sklearn.neighbors import NearestNeighbors

logger = logging.getLogger("pattern_learner")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MODEL_DIR = Path("models")
PATTERN_PATH = MODEL_DIR / "pattern_learner.joblib"
PATTERN_META_PATH = MODEL_DIR / "pattern_metadata.json"

MODEL_VERSION = 4
DEFAULT_WINDOW = 40          # amostras por janela (~10 s a 4 Hz)
DEFAULT_STRIDE = 5           # passo de deslize no treino
MIN_WINDOWS = 30             # mínimo de janelas para aprender algo útil
MAX_ADAPT_FEATURES = 4000    # reservatório de janelas normais p/ adaptação
MIN_ADAPT_FEATURES = 20      # janelas novas antes de um refit incremental
PEAK_FEATURE_IDX = 7         # coluna "p99" em FEATURE_NAMES
MEAN_FEATURE_IDX = 0         # coluna "mean" em FEATURE_NAMES
EPS = 1e-9

FEATURE_NAMES = [
    # amplitude
    "mean", "std", "min", "max", "p10", "p50", "p90", "p99",
    "iqr", "range", "cv",
    # dinâmica
    "delta", "slope", "ac1", "sign_changes",
    # ritmo
    "spec_entropy", "dom_freq", "low_band", "mid_band", "high_band",
    # forma
    "skew", "kurtosis",
]


# ---------------------------------------------------------------------------
# Extração de features (somente a série observada — nenhum rótulo)
# ---------------------------------------------------------------------------

def extract_window_features(values: Sequence[float]) -> np.ndarray:
    """Transforma uma janela de magnitudes de movimento em um vetor de features."""
    v = np.asarray(list(values), dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        v = np.zeros(4, dtype=np.float64)
    elif v.size < 4:
        v = np.pad(v, (0, 4 - v.size), mode="edge")

    n = int(v.size)
    mean = float(np.mean(v))
    std = float(np.std(v))
    vmin = float(np.min(v))
    vmax = float(np.max(v))
    p10, p50, p90, p99 = (float(x) for x in np.percentile(v, [10, 50, 90, 99]))
    q25, q75 = (float(x) for x in np.percentile(v, [25, 75]))
    iqr = q75 - q25
    rng = vmax - vmin
    cv = min(std / (abs(mean) + EPS), 10.0)

    # Dinâmica: tendência e periodicidade (normalizadas pela amplitude,
    # para que máquinas com escalas diferentes sejam comparáveis)
    third = max(1, n // 3)
    delta = float(np.mean(v[-third:]) - np.mean(v[:third])) / (std + EPS)
    if n > 2 and std > EPS:
        t = np.arange(n, dtype=np.float64)
        slope = float(np.polyfit(t, v, 1)[0]) * n / (std + EPS)
    else:
        slope = 0.0

    centered = v - mean
    energy = float(np.sum(centered * centered))
    if n > 2 and energy > EPS:
        ac1 = float(np.sum(centered[:-1] * centered[1:]) / energy)
    else:
        ac1 = 0.0

    d = np.diff(v)
    if d.size > 1:
        sign_changes = float(np.mean(np.abs(np.diff(np.sign(d))) > 0))
    else:
        sign_changes = 0.0

    # Ritmo: espectro normalizado (independe da unidade)
    spec_entropy = dom_freq = low_band = mid_band = high_band = 0.0
    if n >= 8 and energy > EPS:
        spec = np.abs(np.fft.rfft(centered))[1:]
        freqs = np.fft.rfftfreq(n, d=1.0)[1:]
        total = float(np.sum(spec))
        if total > EPS and spec.size > 1:
            p = spec / total
            spec_entropy = float(-np.sum(p * np.log(p + EPS)) / math.log(spec.size))
            dom_freq = float(freqs[int(np.argmax(spec))] / 0.5)  # 0..1 (Nyquist)
            low_band = float(np.sum(spec[freqs < 0.08]) / total)
            mid_mask = (freqs >= 0.08) & (freqs < 0.25)
            mid_band = float(np.sum(spec[mid_mask]) / total)
            high_band = float(np.sum(spec[freqs >= 0.25]) / total)

    if std > EPS:
        z = centered / std
        skew = float(np.mean(z ** 3))
        kurtosis = float(np.mean(z ** 4) - 3.0)
    else:
        skew = kurtosis = 0.0

    return np.array([
        mean, std, vmin, vmax, p10, p50, p90, p99,
        iqr, rng, cv,
        delta, slope, ac1, sign_changes,
        spec_entropy, dom_freq, low_band, mid_band, high_band,
        skew, kurtosis,
    ], dtype=np.float64)


# ---------------------------------------------------------------------------
# Normalização robusta (median/IQR — não depende de labels)
# ---------------------------------------------------------------------------

class RobustScaler:
    """Padronização robusta: (x - mediana) / max(IQR, piso)."""

    def __init__(self):
        self.median: Optional[np.ndarray] = None
        self.scale: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray) -> "RobustScaler":
        X = np.asarray(X, dtype=np.float64)
        self.median = np.median(X, axis=0)
        q25, q75 = np.percentile(X, [25, 75], axis=0)
        iqr = q75 - q25
        # Pisos de escala: features quase constantes (IQR ~ 0) não podem
        # explodir o z-score por causa de ruído de medição.
        floor = np.maximum.reduce([
            0.10 * np.abs(self.median),
            0.25 * np.std(X, axis=0),
            np.full(X.shape[1], 1e-3),
        ])
        scale = np.maximum(iqr, floor)
        scale = np.where(scale > 1e-6, scale, 1.0)
        self.scale = scale
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        if self.median is None or self.scale is None:
            raise RuntimeError("RobustScaler não ajustado")
        return (X - self.median) / self.scale


# ---------------------------------------------------------------------------
# Descoberta do "normal" sem rótulos
# ---------------------------------------------------------------------------

def _knn_distances(Z: np.ndarray, k: int) -> np.ndarray:
    """Distância ao k-ésimo vizinho mais próximo (exclui o próprio ponto)."""
    n = len(Z)
    if n < 2:
        return np.zeros(n, dtype=np.float64)
    k = int(max(1, min(k, n - 1)))
    nn = NearestNeighbors(n_neighbors=k + 1).fit(Z)
    dist, _ = nn.kneighbors(Z)
    return np.asarray(dist[:, -1], dtype=np.float64)


def discover_core(Z: np.ndarray,
                  min_windows: int = MIN_WINDOWS,
                  max_fraction: float = 0.75,
                  min_fraction: float = 0.50) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Descobre o conjunto de janelas que representa o comportamento PADRÃO.

    Estratégia não supervisionada:
      - k-NN em espaço padronizado: comportamento típico = região densa
        (distância pequena ao k-ésimo vizinho);
      - recorte robusto em log(distância): mediana + 3,5 × MAD;
      - fração do núcleo limitada (padrão 50%..95%) para não "aprender" como
        normal uma cauda de alta vibração nem descartar regimes legítimos
        (ex.: máquina parada x em carga).

    Retorna (máscara booleana, estatísticas do recorte).
    """
    n = len(Z)
    if n < min_windows:
        return np.ones(n, dtype=bool), {
            "reason": f"poucas janelas ({n} < {min_windows})",
            "k": 0, "threshold": None,
        }

    k = int(max(3, min(10, n // 20)))
    d = _knn_distances(Z, k)
    logd = np.log(d + EPS)
    med = float(np.median(logd))
    mad = float(np.median(np.abs(logd - med)) * 1.4826)
    spread = mad if mad > 1e-9 else float(np.std(logd))
    threshold = med + 3.5 * spread
    mask = logd <= threshold

    # Garante um núcleo saudável (min_fraction..max_fraction)
    frac = float(np.mean(mask))
    if frac < min_fraction:
        threshold = float(np.percentile(logd, 100.0 * min_fraction))
        mask = logd <= threshold
    elif frac > max_fraction:
        threshold = float(np.percentile(logd, 100.0 * max_fraction))
        mask = logd <= threshold

    return mask, {
        "k": k,
        "log_threshold": float(threshold),
        "distance_threshold": float(np.exp(threshold)),
        "core_fraction": round(float(np.mean(mask)), 4),
        "max_core_fraction": float(max_fraction),
        "median_log_distance": med,
        "mad_log_distance": mad,
    }


# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------

class PatternLearner:
    """
    Aprende e aplica o padrão de movimento da máquina sem rótulos.

    O modelo é persistido em `models/pattern_learner.joblib` e os metadados
    legíveis em `models/pattern_metadata.json`.
    """

    def __init__(self,
                 model_dir: Optional[Path] = None,
                 window: Optional[int] = None,
                 stride: Optional[int] = None,
                 sample_hz: Optional[float] = None):
        # Import tardio para permitir uso standalone sem config/.env
        try:
            from config import config
            self.window = int(window or config.PATTERN_WINDOW)
            self.stride = int(stride or config.PATTERN_STRIDE)
            self.sample_hz = float(sample_hz or config.PATTERN_SAMPLE_HZ)
            self.sensitivity = float(config.PATTERN_SENSITIVITY)
            self.max_core_fraction = float(config.PATTERN_MAX_CORE_FRACTION)
            self.min_core_fraction = float(config.PATTERN_MIN_CORE_FRACTION)
        except Exception:
            self.window = int(window or DEFAULT_WINDOW)
            self.stride = int(stride or DEFAULT_STRIDE)
            self.sample_hz = float(sample_hz or 4.0)
            self.sensitivity = 1.0
            self.max_core_fraction = 0.99
            self.min_core_fraction = 0.50

        self.model_dir = Path(model_dir or MODEL_DIR)
        self.model_path = self.model_dir / PATTERN_PATH.name
        self.meta_path = self.model_dir / PATTERN_META_PATH.name

        self.scaler: Optional[RobustScaler] = None
        self.iforest: Optional[IsolationForest] = None
        self.nn: Optional[NearestNeighbors] = None
        self.kmeans: Optional[KMeans] = None
        self.X_core: Optional[np.ndarray] = None
        self.Z_core: Optional[np.ndarray] = None
        self.k: int = 5
        self.d_median: float = 0.0
        self.d_alert: float = 1.0
        self.d_critical: float = 2.0
        self.z_median: float = 0.0
        self.z_alert: float = 3.0
        self.z_critical: float = 5.0
        self.mz_alert: float = 7.0
        self.mz_critical: float = 12.0
        self.meta: Dict[str, Any] = {}
        self._adapt: Deque[np.ndarray] = deque(maxlen=MAX_ADAPT_FEATURES)
        self._lock = threading.RLock()
        self._load()

    # ---------------- persistência ----------------
    def _load(self) -> None:
        try:
            if not self.model_path.exists():
                return
            payload = joblib.load(self.model_path)
            if payload.get("version") != MODEL_VERSION:
                logger.warning("Modelo de padrão com versão incompatível — ignorado.")
                return
            if int(payload.get("window", self.window)) != self.window:
                logger.warning("Modelo de padrão treinado com outra janela — ignorado.")
                return
            self.scaler = payload["scaler"]
            self.iforest = payload["iforest"]
            self.nn = payload["nn"]
            self.kmeans = payload.get("kmeans")
            self.X_core = payload["X_core"]
            self.Z_core = payload["Z_core"]
            self.k = int(payload.get("k", 5))
            self.d_median = float(payload.get("d_median", 0.0))
            self.d_alert = float(payload.get("d_alert", 1.0))
            self.d_critical = float(payload.get("d_critical", 2.0))
            self.z_median = float(payload.get("z_median", 0.0))
            self.z_alert = float(payload.get("z_alert", 3.0))
            self.z_critical = float(payload.get("z_critical", 5.0))
            self.mz_alert = float(payload.get("mz_alert", 7.0))
            self.mz_critical = float(payload.get("mz_critical", 12.0))
            self.meta = dict(payload.get("meta", {}))
            if self.meta_path.exists():
                try:
                    disk_meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                    self.meta.update(disk_meta)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Falha ao carregar padrão aprendido: {e}")
            self.scaler = None
            self.iforest = None
            self.nn = None
            self.meta = {}

    def _save(self) -> None:
        self.model_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": MODEL_VERSION,
            "window": self.window,
            "stride": self.stride,
            "sample_hz": self.sample_hz,
            "feature_names": FEATURE_NAMES,
            "scaler": self.scaler,
            "iforest": self.iforest,
            "nn": self.nn,
            "kmeans": self.kmeans,
            "X_core": self.X_core,
            "Z_core": self.Z_core,
            "k": self.k,
            "d_median": self.d_median,
            "d_alert": self.d_alert,
            "d_critical": self.d_critical,
            "z_median": self.z_median,
            "z_alert": self.z_alert,
            "z_critical": self.z_critical,
            "mz_alert": self.mz_alert,
            "mz_critical": self.mz_critical,
            "meta": self.meta,
        }
        joblib.dump(payload, self.model_path)
        self.meta_path.write_text(
            json.dumps(self._report_meta(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    # ---------------- treino ----------------
    def _train_from_features(self, X: np.ndarray, extra_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Núcleo do aprendizado: recebe apenas features (sem rótulos)."""
        X = np.asarray(X, dtype=np.float64)
        X = X[np.all(np.isfinite(X), axis=1)]
        if len(X) < MIN_WINDOWS:
            return {
                "ok": False,
                "trained": False,
                "reason": f"dados insuficientes ({len(X)} janelas; mínimo {MIN_WINDOWS})",
                "n_windows": int(len(X)),
            }

        t0 = time.time()
        self.scaler = RobustScaler().fit(X)
        Z = self.scaler.transform(X)

        core_mask, core_info = discover_core(
            Z,
            max_fraction=max(0.30, min(0.995, self.max_core_fraction)),
            min_fraction=max(0.20, min(0.90, self.min_core_fraction)),
        )
        X_core, Z_core = X[core_mask], Z[core_mask]
        if len(X_core) < MIN_WINDOWS:
            # fallback: mantém as janelas mais densas até a fração máxima
            k = int(max(3, min(10, len(Z) // 20)))
            d = _knn_distances(Z, k)
            keep = max(MIN_WINDOWS, min(len(Z), int(self.max_core_fraction * len(Z))))
            idx = np.argsort(d)[:keep]
            core_mask = np.zeros(len(Z), dtype=bool)
            core_mask[idx] = True
            X_core, Z_core = X[core_mask], Z[core_mask]
            core_info["fallback"] = "janelas mais densas"

        k = int(max(3, min(10, len(Z_core) // 20)))
        self.k = k
        self.nn = NearestNeighbors(n_neighbors=min(k, len(Z_core))).fit(Z_core)

        # Distâncias k-NN dentro do próprio núcleo normal (leave-one-out)
        d_core = _knn_distances(Z_core, k)
        self.d_median = float(np.median(d_core))

        # Limiares robustos em z-score (mais estáveis que distância bruta:
        # não dependem da densidade artificial de janelas sobrepostas).
        core_rms = np.sqrt(np.mean(Z_core ** 2, axis=1))
        core_max = np.max(np.abs(Z_core), axis=1)
        z_median = float(np.median(core_rms))
        z_alert = max(float(np.percentile(core_rms, 99)), 4.0)
        z_critical = max(float(np.percentile(core_rms, 99.9)), 8.0, z_alert * 1.5)
        mz_alert = max(float(np.percentile(core_max, 99)), 10.0)
        mz_critical = max(float(np.percentile(core_max, 99.9)), 20.0, mz_alert * 1.5)
        # Sensibilidade maior → limites menores (detecta desvios mais sutis)
        sens = max(0.10, float(self.sensitivity))
        self.z_alert = max(z_alert / sens, 2.0)
        self.z_critical = max(z_critical / sens, self.z_alert * 1.25)
        self.mz_alert = max(mz_alert / sens, 4.0)
        self.mz_critical = max(mz_critical / sens, self.mz_alert * 1.25)
        self.z_median = z_median

        # Distância k-NN: mantida para diagnóstico de novidade estrutural
        self.d_alert = max(float(np.percentile(d_core, 99)), self.d_median * 1.5)
        self.d_critical = max(float(np.percentile(d_core, 99.9)), self.d_alert * 1.5)

        # IsolationForest treinado APENAS no comportamento padrão
        self.iforest = IsolationForest(
            n_estimators=200,
            contamination=0.02,
            random_state=42,
            n_jobs=-1,
        ).fit(Z_core)

        # Regimes de operação normal (padrões de movimento recorrentes)
        n_regimes = int(min(4, max(1, len(X_core) // 40)))
        self.kmeans = None
        regimes: List[Dict[str, Any]] = []
        if n_regimes > 1:
            self.kmeans = KMeans(n_clusters=n_regimes, n_init=10, random_state=42).fit(Z_core)
            labels = self.kmeans.labels_
            for c in range(n_regimes):
                idx = labels == c
                if not np.any(idx):
                    continue
                regimes.append({
                    "id": int(c),
                    "windows": int(idx.sum()),
                    "peak_median": round(float(np.median(X_core[idx, PEAK_FEATURE_IDX])), 4),
                    "mean_median": round(float(np.median(X_core[idx, MEAN_FEATURE_IDX])), 4),
                })
        else:
            regimes.append({
                "id": 0,
                "windows": int(len(X_core)),
                "peak_median": round(float(np.median(X_core[:, PEAK_FEATURE_IDX])), 4),
                "mean_median": round(float(np.median(X_core[:, MEAN_FEATURE_IDX])), 4),
            })

        self.X_core = X_core
        self.Z_core = Z_core
        self._adapt.clear()

        meta = {
            "trained": True,
            "trained_at": datetime.utcnow().isoformat(),
            "train_seconds": round(time.time() - t0, 2),
            "window": self.window,
            "stride": self.stride,
            "sample_hz": self.sample_hz,
            "feature_names": FEATURE_NAMES,
            "n_windows": int(len(X)),
            "n_core": int(len(X_core)),
            "core_fraction": round(float(len(X_core) / len(X)), 4),
            "n_regimes": int(len(regimes)),
            "regimes": regimes,
            "knn_k": int(k),
            "thresholds": {
                "rms_z_alert": round(self.z_alert, 6),
                "rms_z_critical": round(self.z_critical, 6),
                "max_z_alert": round(self.mz_alert, 6),
                "max_z_critical": round(self.mz_critical, 6),
                "distance_median": round(self.d_median, 6),
                "distance_alert": round(self.d_alert, 6),
                "distance_critical": round(self.d_critical, 6),
            },
            "core_discovery": core_info,
            "algorithm": "kNN density + recorte robusto + IsolationForest + KMeans (não supervisionado)",
            "uses_labels": False,
            "sensitivity": self.sensitivity,
            "n_adapt_windows": 0,
        }
        if extra_meta:
            meta.update(extra_meta)
        self.meta = meta
        self._save()
        logger.info(
            "Padrão aprendido: %d janelas (%d no núcleo, %.0f%%), %d regimes, "
            "z-alerta=%.2f z-crítico=%.2f",
            len(X), len(X_core), 100.0 * len(X_core) / len(X),
            len(regimes), self.z_alert, self.z_critical,
        )
        return {"ok": True, **self._report_meta()}

    def learn(self,
              values: Sequence[float],
              timestamps: Optional[Sequence[Any]] = None,
              extra_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Aprende o padrão a partir de uma série observada (apenas valores).

        `timestamps` é opcional e serve somente para não misturar gravações
        separadas por longos intervalos (reinícios/paradas).
        """
        with self._lock:
            X, n_segments = windows_from_series(
                values, timestamps, self.window, self.stride
            )
            # Observações curtas (ex.: 60 s ao vivo) geram poucas janelas no
            # passo padrão. Reduzimos o passo só o suficiente para aprender.
            used_stride = self.stride
            if len(X) < MIN_WINDOWS:
                for candidate in range(self.stride - 1, 0, -1):
                    X_alt, n_segments_alt = windows_from_series(
                        values, timestamps, self.window, candidate
                    )
                    if len(X_alt) >= MIN_WINDOWS:
                        X, n_segments, used_stride = X_alt, n_segments_alt, candidate
                        break
                    X, n_segments = X_alt, n_segments_alt
                    used_stride = candidate
            meta = dict(extra_meta or {})
            meta.update({
                "n_samples": int(len(values)),
                "n_segments": int(n_segments),
                "learning_stride": int(used_stride),
            })
            return self._train_from_features(X, meta)

    # ---------------- inferência ----------------
    def score(self, values: Sequence[float]) -> Dict[str, Any]:
        """Compara a janela observada com o padrão aprendido (sem rótulos)."""
        with self._lock:
            if self.nn is None or self.scaler is None or self.iforest is None:
                return {
                    "trained": False,
                    "level": "NORMAL",
                    "reason": "padrão ainda não aprendido",
                }

            feats = extract_window_features(values)
            z = self.scaler.transform(feats.reshape(1, -1))
            z_flat = z.ravel()
            rms_z = float(np.sqrt(np.mean(z_flat ** 2)))
            max_z = float(np.max(np.abs(z_flat)))

            k = int(max(1, min(self.k, len(self.Z_core))))
            try:
                dist = float(self.nn.kneighbors(z, n_neighbors=k)[0][0][-1])
            except Exception:
                dist = float(rms_z)

            iforest_score = float(self.iforest.decision_function(z)[0])
            iforest_anomaly = bool(self.iforest.predict(z)[0] == -1)

            anomaly = float(np.clip(
                max(rms_z / max(self.z_critical, EPS),
                    max_z / max(self.mz_critical, EPS)),
                0.0, 1.5,
            ))

            # IsolationForest só reforça o alerta quando há também um desvio
            # robusto mínimo (evita falsos positivos em treinos curtos).
            iforest_signal = iforest_anomaly and (
                rms_z >= self.z_alert * 0.6 or max_z >= self.mz_alert * 0.6
            )

            if rms_z >= self.z_critical or max_z >= self.mz_critical:
                level = "CRITICO"
            elif rms_z >= self.z_alert or max_z >= self.mz_alert or iforest_signal:
                level = "ALERTA"
            else:
                level = "NORMAL"

            regime = 0
            if self.kmeans is not None:
                regime = int(self.kmeans.predict(z)[0])

            peak = float(np.max(np.asarray(values, dtype=np.float64))) if len(values) else 0.0
            confidence = 0.85 if level == "NORMAL" else round(min(0.99, 0.55 + 0.35 * anomaly), 2)

            return {
                "trained": True,
                "level": level,
                "anomaly_index": round(anomaly, 4),
                "rms_z": round(rms_z, 4),
                "max_z": round(max_z, 4),
                "rms_z_alert": round(self.z_alert, 4),
                "rms_z_critical": round(self.z_critical, 4),
                "max_z_alert": round(self.mz_alert, 4),
                "max_z_critical": round(self.mz_critical, 4),
                "distance": round(dist, 6),
                "distance_alert": round(self.d_alert, 6),
                "distance_critical": round(self.d_critical, 6),
                "is_anomaly": bool(level != "NORMAL"),
                "iforest_anomaly": iforest_anomaly,
                "iforest_score": round(iforest_score, 6),
                "regime": regime,
                "peak_magnitude": round(peak, 4),
                "confidence": confidence,
                "trained_at": self.meta.get("trained_at"),
                "uses_labels": False,
            }

    # ---------------- adaptação incremental ----------------
    def add_normal_window(self, values: Sequence[float]) -> bool:
        """Guarda uma janela confirmada como normal para adaptação futura."""
        with self._lock:
            if self.nn is None:
                return False
            self._adapt.append(extract_window_features(values))
            return len(self._adapt) >= MIN_ADAPT_FEATURES

    def refit_from_adapt(self) -> Dict[str, Any]:
        """
        Reaprende incorporando o comportamento normal recente (drift natural):
        a máquina pode mudar de carga/velocidade e o padrão acompanha.
        """
        with self._lock:
            if self.nn is None or self.X_core is None:
                return {"ok": False, "reason": "modelo não treinado"}
            if len(self._adapt) < MIN_ADAPT_FEATURES:
                return {
                    "ok": False,
                    "reason": f"poucas janelas novas ({len(self._adapt)} < {MIN_ADAPT_FEATURES})",
                }
            adapt_X = np.vstack(list(self._adapt))
            X = np.vstack([self.X_core, adapt_X])
            # Limita o custo: mantém no máximo 3000 janelas de núcleo
            if len(X) > 3000:
                idx = np.linspace(0, len(X) - 1, 3000).astype(int)
                X = X[idx]
            n_adapt = len(adapt_X)
            previous_adapt = int(self.meta.get("n_adapt_windows", 0))
            self._adapt.clear()
            result = self._train_from_features(X, {"adapted_at": datetime.utcnow().isoformat()})
            if result.get("ok"):
                self.meta["n_adapt_windows"] = previous_adapt + n_adapt
                self.meta["last_adapt_windows"] = n_adapt
                self._save()
            return result

    # ---------------- status ----------------
    def _report_meta(self) -> Dict[str, Any]:
        m = dict(self.meta)
        m["model_path"] = str(self.model_path)
        m["module"] = "pattern_learner"
        m["uses_labels"] = False
        return m

    def status(self) -> Dict[str, Any]:
        with self._lock:
            if not self.meta.get("trained"):
                return {
                    "trained": False,
                    "module": "pattern_learner",
                    "uses_labels": False,
                    "reason": "padrão ainda não aprendido (python learn_pattern.py ou POST /ml/pattern/train)",
                    "model_path": str(self.model_path),
                }
            return self._report_meta()

    def reset(self) -> Dict[str, Any]:
        """Esquece o padrão aprendido (útil ao trocar de máquina/peça)."""
        with self._lock:
            self.scaler = None
            self.iforest = None
            self.nn = None
            self.kmeans = None
            self.X_core = None
            self.Z_core = None
            self.meta = {}
            self._adapt.clear()
            for p in (self.model_path, self.meta_path):
                try:
                    if p.exists():
                        p.unlink()
                except Exception as e:
                    logger.warning(f"Falha ao remover {p}: {e}")
            return {"ok": True, "reset": True, "trained": False}


# ---------------------------------------------------------------------------
# Janelas / segmentação (evita misturar gravações separadas)
# ---------------------------------------------------------------------------

def windows_from_series(values: Sequence[float],
                        timestamps: Optional[Sequence[Any]] = None,
                        window: int = DEFAULT_WINDOW,
                        stride: int = DEFAULT_STRIDE) -> Tuple[np.ndarray, int]:
    """Extrai features de janelas deslizantes; não cruza lacunas temporais."""
    v = np.asarray(values, dtype=np.float64)
    keep = np.isfinite(v)
    v = v[keep]
    ts = None
    if timestamps is not None:
        ts = list(timestamps)
        ts = [t for t, k in zip(ts, keep) if k]
        if len(ts) != len(v):
            ts = None

    segments: List[Tuple[int, int]] = []
    if ts and len(ts) > 2:
        secs = np.array([_to_epoch(t) for t in ts], dtype=np.float64)
        dt = np.diff(secs)
        dt = dt[np.isfinite(dt) & (dt >= 0)]
        median_dt = float(np.median(dt)) if dt.size else 0.25
        gap_limit = max(5.0, 20.0 * median_dt)
        start = 0
        for i in range(1, len(secs)):
            if secs[i] - secs[i - 1] > gap_limit:
                segments.append((start, i))
                start = i
        segments.append((start, len(secs)))
    else:
        segments.append((0, len(v)))

    feats: List[np.ndarray] = []
    for start, end in segments:
        seg = v[start:end]
        if len(seg) < window:
            continue
        for i in range(0, len(seg) - window + 1, max(1, stride)):
            feats.append(extract_window_features(seg[i:i + window]))

    if not feats:
        return np.empty((0, len(FEATURE_NAMES))), len(segments)
    return np.vstack(feats), len(segments)


def _to_epoch(t: Any) -> float:
    """Converte datetime/ISO/epoch para segundos."""
    if isinstance(t, (int, float, np.integer, np.floating)):
        return float(t)
    if isinstance(t, datetime):
        return t.timestamp()
    if isinstance(t, str):
        try:
            return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return 0.0


# ---------------------------------------------------------------------------
# Aprendizado a partir do histórico (SQLite) — sem usar labels/status
# ---------------------------------------------------------------------------

def load_db_observations(db_path: str, limit: int = 50000) -> Tuple[List[float], List[Any], Dict[str, Any]]:
    """Lê apenas timestamp + value (IGNORA status/rótulos) do SQLite."""
    import sqlite3

    info: Dict[str, Any] = {"db": db_path, "rows": 0}
    if not Path(db_path).exists():
        return [], [], info

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT timestamp, value FROM (
                SELECT timestamp, value
                FROM events
                WHERE value IS NOT NULL
                  AND value >= 0
                  AND value < 100
                  AND (source IS NULL OR source != 'manual')
                ORDER BY timestamp DESC
                LIMIT ?
            ) ORDER BY timestamp ASC
            """,
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()

    values = [float(r["value"]) for r in rows]
    timestamps = [r["timestamp"] for r in rows]
    info["rows"] = len(rows)
    return values, timestamps, info


def learn_from_history(db_path: str, limit: int = 50000) -> Dict[str, Any]:
    values, timestamps, info = load_db_observations(db_path, limit)
    if not values:
        return {"ok": False, "reason": f"nenhuma observação em {db_path}"}
    return pattern_learner.learn(values, timestamps, extra_meta={"source": "sqlite", **info})


# ---------------------------------------------------------------------------
# Aprendizado ao vivo (webcam / RTSP / vídeo / mock) — apenas observando
# ---------------------------------------------------------------------------

def collect_live_values(source: Any,
                        seconds: float = 30.0,
                        sample_hz: Optional[float] = None,
                        max_frames: Optional[int] = None) -> Tuple[List[float], List[float]]:
    """
    Observa a fonte de vídeo e coleta o índice de movimento a `sample_hz`.

    Não usa thresholds nem rótulos: apenas registra o que a câmera vê.
    """
    import cv2

    sample_hz = float(sample_hz or pattern_learner.sample_hz)
    interval = 1.0 / max(0.5, sample_hz)

    use_mock = isinstance(source, str) and source.lower() == "mock"
    mock = None
    cap = None
    if use_mock:
        # Import tardio para evitar ciclo (vision_monitor importa este módulo)
        from vision_monitor import MockCamera, FarnebackDetector
        mock = MockCamera()
        detector = FarnebackDetector(roi=None)
    else:
        from vision_monitor import FarnebackDetector
        detector = FarnebackDetector(roi=tuple(_roi_from_config()) if _roi_from_config() else None)
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir a fonte de vídeo: {source}")

    values: List[float] = []
    times: List[float] = []
    try:
        target_fps = 30
        try:
            from config import config as _cfg
            target_fps = max(1, int(_cfg.TARGET_FPS))
        except Exception:
            pass
        target_dt = 1.0 / target_fps

        t0 = time.time()
        last_sample = 0.0
        frames = 0
        while True:
            loop_t = time.time()
            now = loop_t
            if now - t0 >= seconds:
                break
            if use_mock:
                ok, frame = mock.read()
            else:
                ok, frame = cap.read()
                if not ok:
                    # vídeo terminou: reinicia para continuar observando
                    if isinstance(source, str) and Path(source).exists():
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, frame = cap.read()
                    if not ok:
                        break

            if frame is None:
                continue
            frames += 1
            magnitude = detector.process(frame)
            if now - last_sample >= interval:
                last_sample = now
                values.append(float(magnitude))
                times.append(now - t0)
            if max_frames is not None and frames >= max_frames:
                break
            # Mantém a mesma cadência do Vision Monitor (30 fps por padrão)
            remaining = target_dt - (time.time() - loop_t)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        if cap is not None:
            cap.release()
        if mock is not None:
            mock.release()

    return values, times


def _roi_from_config():
    try:
        from config import config
        return config.ROI_COORDS
    except Exception:
        return None


def learn_live(source: Any,
               seconds: float = 30.0,
               sample_hz: Optional[float] = None) -> Dict[str, Any]:
    values, times = collect_live_values(source, seconds=seconds, sample_hz=sample_hz)
    if len(values) < pattern_learner.window + 5:
        return {
            "ok": False,
            "reason": f"poucas amostras coletadas ({len(values)}); aumente --seconds",
            "n_samples": len(values),
        }
    return pattern_learner.learn(
        values,
        times,
        extra_meta={"source": str(source), "collection_seconds": seconds, "sample_hz": sample_hz or pattern_learner.sample_hz},
    )


# ---------------------------------------------------------------------------
# Singleton usado pelo vision_monitor, API e CLI
# ---------------------------------------------------------------------------

pattern_learner = PatternLearner()
