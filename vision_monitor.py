"""
vision_monitor.py
Módulo de Visão Computacional para manutenção preditiva e segurança industrial (E-Stop).

Implementa:
  - Captura resiliente: webcam local (0) ou stream RTSP com reconexão automática.
  - Mock camera (modo simulado) para testes sem hardware real.
  - Região de Interesse (ROI) [x, y, w, h] para isolar a peça móvel.
  - Calibração inicial de baseline (N segundos) para estabelecer movimento normal.
  - Dense Optical Flow Farnebäck (cv2.calcOpticalFlowFarneback) para índice de vibração.
  - Máquina de estados com debounce: NORMAL | ALERTA | CRÍTICO.
  - Envio não-bloqueante (threads separadas) para API, notificações e ESP32.
  - HUD OpenCV com FPS, ROI, métrica atual e status.

Uso:
    python vision_monitor.py
"""

import cv2
import numpy as np
import asyncio
import threading
import time
import os
import json
import logging
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Dict, Any
from collections import deque

from config import config
from pattern_learner import pattern_learner

# ---------------------------------------------------------------------------
# Diretórios (garantir Path)
# ---------------------------------------------------------------------------
CAPTURES_DIR = Path(config.CAPTURES_DIR)
LOGS_DIR = Path(config.LOGS_DIR)

# ---------------------------------------------------------------------------
# Diretórios (garantir Path + criação antes do logging)
# ---------------------------------------------------------------------------
CAPTURES_DIR = Path(config.CAPTURES_DIR)
LOGS_DIR = Path(config.LOGS_DIR)
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "vision_monitor.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("vision_monitor")

# ---------------------------------------------------------------------------
# Constantes de estado
# ---------------------------------------------------------------------------
class State:
    NORMAL = "NORMAL"
    ALERTA = "ALERTA"
    CRITICO = "CRITICO"

# ---------------------------------------------------------------------------
# Detector de anomalias com Dense Optical Flow (Farnebäck)
# ---------------------------------------------------------------------------
class FarnebackDetector:
    """
    Calcula o fluxo óptico denso (Farnebäck) entre o frame anterior e o atual,
    restrito à ROI, e devolve um índice contínuo de vibração (magnitude média
    do fluxo em pixels/deslocamento normalizado).
    """

    def __init__(self, roi: Optional[Tuple[int, int, int, int]] = None):
        self.roi = roi
        self.prev_gray: Optional[np.ndarray] = None
        # Parâmetros do Farnebäck vindos do config
        self.pyr_scale = config.FLOW_PYR_SCALE
        self.levels = config.FLOW_LEVELS
        self.winsize = config.FLOW_WINSIZE
        self.iterations = config.FLOW_ITERATIONS
        self.poly_n = config.FLOW_POLY_N
        self.poly_sigma = config.FLOW_POLY_SIGMA
        self.flags = config.FLOW_FLAGS
        # Análise em largura reduzida (Farnebäck em 720p+ é caro demais p/ tempo real).
        self.analysis_width = 320
        self._clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        # Grade 3x3 de vigilância: última medição (p99 por célula, célula quente, direção)
        self.last_grid: Optional[np.ndarray] = None
        self.last_hot: Tuple[int, int] = (1, 1)  # (row, col)
        self.last_direction: str = ""
        self.last_hot_mag: float = 0.0
        self.last_angle_deg: float = 0.0
        self.last_index: float = 0.0

    def _to_gray(self, frame: np.ndarray) -> np.ndarray:
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        # Blur primeiro (ruído) → CLAHE depois (contraste) — visão em luz normal e IR
        gray = cv2.GaussianBlur(gray, (5, 5), 1.0)
        return self._clahe.apply(gray)

    def _crop_roi(self, gray: np.ndarray) -> np.ndarray:
        if self.roi is None:
            return gray
        x, y, w, h = self.roi
        h_, w_ = gray.shape
        x = max(0, min(x, w_ - 1))
        y = max(0, min(y, h_ - 1))
        w = max(1, min(w, w_ - x))
        h = max(1, min(h, h_ - y))
        return gray[y:y + h, x:x + w]

    def compute_flow(self, prev: np.ndarray, curr: np.ndarray) -> np.ndarray:
        """Retorna o fluxo óptico denso (H, W, 2) entre prev e curr."""
        flow = cv2.calcOpticalFlowFarneback(
            prev,
            curr,
            None,
            self.pyr_scale,
            self.levels,
            self.winsize,
            self.iterations,
            self.poly_n,
            self.poly_sigma,
            self.flags,
        )
        return flow

    _GRID_LABELS = (("superior-esquerda", "superior-centro", "superior-direita"),
                    ("meio-esquerda", "centro", "meio-direita"),
                    ("inferior-esquerda", "inferior-centro", "inferior-direita"))
    _DIR_ARROWS = (("→", 0), ("↘", 45), ("↓", 90), ("↙", 135),
                   ("←", 180), ("↖", 225), ("↑", 270), ("↗", 315))

    def _analyze_grid(self, flow: np.ndarray) -> None:
        """Grade 3x3: p99 de magnitude por célula, célula mais agitada e direção dominante."""
        h, w = flow.shape[:2]
        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        grid = np.zeros((3, 3), dtype=np.float32)
        ch, cw = max(1, h // 3), max(1, w // 3)
        for r in range(3):
            for c in range(3):
                cell = mag[r * ch:(r + 1) * ch if r < 2 else h,
                           c * cw:(c + 1) * cw if c < 2 else w]
                if cell.size:
                    grid[r, c] = float(np.percentile(cell, 99))
        self.last_grid = grid
        hot = np.unravel_index(int(np.argmax(grid)), grid.shape)
        self.last_hot = (int(hot[0]), int(hot[1]))
        self.last_hot_mag = float(grid[hot])
        hx0, hy0 = hot[1] * cw, hot[0] * ch
        hx1 = hx0 + (cw if hot[1] < 2 else w - hx0)
        hy1 = hy0 + (ch if hot[0] < 2 else h - hy0)
        cell_flow = flow[hy0:hy1, hx0:hx1]
        mx, my = float(cell_flow[..., 0].mean()), float(cell_flow[..., 1].mean())
        angle = float(np.degrees(np.arctan2(my, mx))) % 360.0
        self.last_angle_deg = angle
        best, best_d = "", 999.0
        for arrow, center in self._DIR_ARROWS:
            d = abs((angle - center + 180) % 360 - 180)
            if d < best_d:
                best_d, best = d, arrow
        self.last_direction = best

    def process(self, frame: np.ndarray) -> float:
        """
        Processa um frame e retorna o índice de vibração (percentil 99 da magnitude).
        No primeiro frame, apenas armazena baseline e retorna 0.0.
        """
        gray = self._to_gray(frame)
        roi_gray = self._crop_roi(gray)

        # Filtro gaussiano para reduzir ruído
        roi_gray = cv2.GaussianBlur(roi_gray, (5, 5), 1.0)

        # Reduz para análise rápida (fluído em tempo real)
        h, w = roi_gray.shape
        if w > self.analysis_width:
            scale = self.analysis_width / float(w)
            roi_gray = cv2.resize(roi_gray, (self.analysis_width, max(1, int(round(h * scale)))),
                                  interpolation=cv2.INTER_AREA)

        if self.prev_gray is None:
            self.prev_gray = roi_gray
            self.last_index = 0.0
            return 0.0

        # Garante mesma resolução
        if self.prev_gray.shape != roi_gray.shape:
            self.prev_gray = cv2.resize(self.prev_gray, (roi_gray.shape[1], roi_gray.shape[0]))

        # Normalização de ganho por frame: a auto-exposição da webcam muda o brilho
        # global constantemente; normalizar a média para 128 torna o fluxo imune a isso
        # SEM pular frames (pular frames era o que fazia a detecção "parar" às vezes).
        def gain_normalize(img: np.ndarray) -> np.ndarray:
            m = float(img.mean())
            if m < 1.0:
                return img
            return np.clip(img.astype(np.float32) * (128.0 / m), 0, 255).astype(np.uint8)

        roi_gray = gain_normalize(roi_gray)
        if self.prev_gray.shape == roi_gray.shape:
            prev_norm = gain_normalize(self.prev_gray)
        else:
            prev_norm = self.prev_gray

        flow = self.compute_flow(prev_norm, roi_gray)

        # Magnitude do fluxo
        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)

        # Índice robusto de vibração: percentil 99 da magnitude.
        # Foca na região em movimento dominante (punha/mancais) e é
        # insensível ao fundo estático e ao ruído de sensor.
        index = float(np.percentile(mag, 99))
        self.last_index = index

        # Localização do movimento (célula quente + direção)
        self._analyze_grid(flow)

        # Atualiza baseline
        self.prev_gray = roi_gray
        return index

    @property
    def last_region(self) -> str:
        r, c = self.last_hot
        return self._GRID_LABELS[r][c]


# ---------------------------------------------------------------------------
# Serviço de notificações (Discord/Slack webhook + Telegram) com cooldown
# ---------------------------------------------------------------------------
class Notifier:
    """Envia alertas via webhook (Discord/Slack) e/ou Telegram Bot API."""

    def __init__(self):
        self.webhook_url = config.WEBHOOK_URL
        self.telegram_token = config.TELEGRAM_BOT_TOKEN
        self.telegram_chat_id = config.TELEGRAM_CHAT_ID
        self.alert_cooldown = config.ALERT_COOLDOWN_SECONDS
        self.critical_cooldown = config.CRITICAL_COOLDOWN_SECONDS
        self._last_alert: Optional[datetime] = None
        self._last_critical: Optional[datetime] = None
        self._lock = threading.Lock()

    def _cooldown_ok(self, status: str, now: datetime) -> bool:
        if status == State.ALERTA:
            if self._last_alert is None:
                return True
            return (now - self._last_alert).total_seconds() >= self.alert_cooldown
        if status == State.CRITICO:
            if self._last_critical is None:
                return True
            return (now - self._last_critical).total_seconds() >= self.critical_cooldown
        return False

    def _mark_sent(self, status: str, now: datetime):
        if status == State.ALERTA:
            self._last_alert = now
        elif status == State.CRITICO:
            self._last_critical = now

    def send(self, status: str, value: float, frame_path: Optional[str] = None):
        """Thread-safe: envia notificação se cooldown permitir."""
        now = datetime.now(timezone.utc)
        with self._lock:
            if not self._cooldown_ok(status, now):
                logger.debug(f"Cooldown ativo para {status}, notificação suprimida.")
                return
            self._mark_sent(status, now)

        msg = (
            f"🚨 **ALERTA INDUSTRIAL** 🚨\n"
            f"Status: **{status}**\n"
            f"Magnitude: `{value:.3f}`\n"
            f"Horário (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )

        # Webhook (Discord/Slack)
        if self.webhook_url:
            threading.Thread(target=self._send_webhook, args=(msg, frame_path), daemon=True).start()

        # Telegram
        if self.telegram_token and self.telegram_chat_id:
            threading.Thread(target=self._send_telegram, args=(msg, frame_path), daemon=True).start()

    def _send_webhook(self, message: str, frame_path: Optional[str]):
        try:
            is_discord = "discord" in self.webhook_url.lower()
            if frame_path and Path(frame_path).exists() and is_discord:
                # Discord aceita multipart com arquivo
                with open(frame_path, "rb") as f:
                    files = {"file": (Path(frame_path).name, f, "image/jpeg")}
                    data = {"content": message}
                    requests.post(self.webhook_url, data=data, files=files, timeout=10)
            else:
                payload = {"content": message} if is_discord else {"text": message}
                requests.post(self.webhook_url, json=payload, timeout=10)
            logger.info(f"Webhook enviado: {message[:60]}...")
        except Exception as e:
            logger.error(f"Falha ao enviar webhook: {e}")

    def _send_telegram(self, message: str, frame_path: Optional[str]):
        try:
            base = f"https://api.telegram.org/bot{self.telegram_token}"
            # Texto
            requests.post(
                f"{base}/sendMessage",
                json={"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=10,
            )
            # Foto (se houver)
            if frame_path and Path(frame_path).exists():
                with open(frame_path, "rb") as f:
                    files = {"photo": (Path(frame_path).name, f, "image/jpeg")}
                    data = {"chat_id": self.telegram_chat_id, "caption": message}
                    requests.post(f"{base}/sendPhoto", data=data, files=files, timeout=15)
            logger.info("Telegram enviado.")
        except Exception as e:
            logger.error(f"Falha ao enviar Telegram: {e}")


# ---------------------------------------------------------------------------
# Cliente ESP32 (proxy para API)
# ---------------------------------------------------------------------------
class ESP32Client:
    """Envia comandos ao ESP32 via API (proxy) ou diretamente."""

    def __init__(self):
        self.base_url = f"http://{config.ESP32_IP}:{config.ESP32_PORT}"
        self.timeout = config.ESP32_TIMEOUT_SECONDS
        self._lock = threading.Lock()

    def emergency_stop(self) -> Dict[str, Any]:
        with self._lock:
            try:
                r = requests.post(f"{self.base_url}/emergency-stop", json={}, timeout=self.timeout)
                return r.json()
            except Exception as e:
                logger.error(f"ESP32 emergency-stop falhou: {e}")
                return {"success": False, "error": str(e)}

    def reset(self) -> Dict[str, Any]:
        with self._lock:
            try:
                r = requests.post(f"{self.base_url}/reset", json={}, timeout=self.timeout)
                return r.json()
            except Exception as e:
                logger.error(f"ESP32 reset falhou: {e}")
                return {"success": False, "error": str(e)}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            try:
                r = requests.get(f"{self.base_url}/status", timeout=self.timeout)
                return r.json()
            except Exception as e:
                logger.error(f"ESP32 status falhou: {e}")
                return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Sensor de Temperatura (monitoramento térmico / câmera com sensor)
# ---------------------------------------------------------------------------
class TemperatureSensor:
    """
    Fonte unificada de temperatura para monitoramento térmico.

    TEMP_SOURCE:
      - mock    → temperatura simulada (baseada no modo da câmera simulada:
                   normal / alerta / crítico; sobe com o estresse térmico).
      - file    → lê a última temperatura registrada em um arquivo de log
                   (ex: logs/temperature.log) — linha JSON {"t":..., "temp":...}
                   ou valor numérico.
      - sensor  → consulta o ESP32 (ou outro dispositivo) via /status
                   esperando campo "temperature".
    """

    def __init__(self, mock_mode: str = "normal"):
        self.source = (config.TEMP_SOURCE or "mock").lower()
        self.mock_mode = mock_mode
        self.temp_max_alerta = config.TEMP_MAX_ALERTA
        self.temp_max_critico = config.TEMP_MAX_CRITICO
        self.sensor_client = ESP32Client()
        self.last_temp = 0.0
        self.reads = 0
        self._lock = threading.Lock()

    def _mock_temperature(self) -> float:
        base = {"normal": 38.0, "alerta": 50.0, "critico": 68.0}.get(self.mock_mode, 38.0)
        return float(base + np.random.normal(0, 0.8))

    def _read_from_file(self) -> float:
        try:
            path = Path(config.TEMP_FILE_PATH)
            if not path.exists():
                return 0.0
            lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            if not lines:
                return 0.0
            last = lines[-1]
            val = None
            try:
                val = float(json.loads(last).get("temperature") or json.loads(last).get("temp") or 0.0)
            except Exception:
                val = float(last)
            return float(val)
        except Exception:
            return 0.0

    def read(self) -> float:
        """Lê a temperatura atual (°C). Thread-safe."""
        with self._lock:
            temp = 0.0
            if self.source == "mock":
                temp = self._mock_temperature()
            elif self.source == "file":
                temp = self._read_from_file()
            elif self.source == "sensor":
                try:
                    status = self.sensor_client.status()
                    temp = float(status.get("temperature") or 0.0)
                except Exception:
                    temp = 0.0
            self.last_temp = temp
            self.reads += 1
            return float(temp)


class SidecarTelemetry:
    """
    Telemetria pareada de um vídeo de simulação.

    Se existir um arquivo `<nome>.telemetry.jsonl` ao lado do vídeo em
    VIDEO_SOURCE, cada linha JSON deve conter pelo menos {"frame", "temperature"}.
    O monitor ancora a temperatura no índice do frame atual — sincronia perfeita
    entre o que é visto e o que é medido (ex: o display térmico do vídeo).
    """

    def __init__(self, video_path: str):
        self.points: Optional[List[dict]] = None
        path = Path(str(video_path)).with_suffix(".telemetry.jsonl")
        if not path.exists():
            return
        points: List[Optional[dict]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    idx = int(data.get("frame", len(points)))
                    while len(points) <= idx:
                        points.append(None)
                    points[idx] = data
            self.points = [p for p in points]  # índice = frame; None = sem dado
            logger.info(f"Sidecar de telemetria carregado: {path} ({sum(1 for p in self.points if p)} pontos)")
        except Exception as e:
            logger.warning(f"Falha ao carregar sidecar {path}: {e} — usando TEMP_SOURCE como antes.")
            self.points = None

    @property
    def available(self) -> bool:
        return bool(self.points)

    def temperature_at(self, frame_idx: int) -> Optional[float]:
        if not self.points:
            return None
        frame_idx = max(0, min(frame_idx, len(self.points) - 1))
        point = self.points[frame_idx]
        if not point:
            return None
        try:
            return float(point["temperature"])
        except (KeyError, TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Mock Camera (para testes no Codespaces sem webcam)
# ---------------------------------------------------------------------------
class MockCamera:
    """
    Gera frames sintéticos com movimento controlado para simular vibração.
    Modo 'normal': objeto estático com leve ruído.
    Modo 'alerta': oscilação suave senoidal.
    Modo 'critico': oscilação rápida e aleatória (simula falha).
    """

    def __init__(self, width: int = 640, height: int = 480):
        self.width = width
        self.height = height
        self.mode = "normal"
        self.t = 0.0
        # Objeto simulado (peça móvel)
        self.base_pos = np.array([width // 2, height // 2], dtype=np.float32)

    def set_mode(self, mode: str):
        if mode in ("normal", "alerta", "critico"):
            self.mode = mode

    def read(self) -> Tuple[bool, np.ndarray]:
        self.t += 0.05
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        # Fundo cinza
        img[:] = (40, 40, 40)

        # Posição da peça (amplitudes calibradas para o Farnebäck:
        # ~0.1 px/frame normal, ~2 px/frame alerta, ~4-6 px/frame crítico)
        pos = self.base_pos.copy()
        if self.mode == "normal":
            pos += np.array([np.sin(self.t * 0.5) * 2, np.cos(self.t * 0.3) * 2])
        elif self.mode == "alerta":
            pos += np.array([np.sin(self.t * 9.0) * 10, np.cos(self.t * 8.0) * 10])
        elif self.mode == "critico":
            pos += np.array([
                np.sin(self.t * 14.5) * 12 + np.random.normal(0, 3.0),
                np.cos(self.t * 12.5) * 12 + np.random.normal(0, 3.0),
            ])

        # Desenha peça (círculo vermelho)
        cv2.circle(img, (int(pos[0]), int(pos[1])), 40, (0, 0, 255), -1)
        # Linha de referência
        cv2.line(img, (0, self.height // 2), (self.width, self.height // 2), (100, 100, 100), 1)

        # Ruído leve
        noise = np.random.normal(0, 3, img.shape).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        return True, img

    def release(self):
        pass


# ---------------------------------------------------------------------------
# VisionMonitor principal
# ---------------------------------------------------------------------------
class VisionMonitor:
    """
    Loop principal de monitoramento:
      - Captura frame (real ou mock)
      - Calcula índice Farnebäck
      - Atualiza estado com debounce
      - Salva capture em disco
      - Envia evento para API (thread separada)
      - Dispara notificação (thread separada)
      - Comanda ESP32 em CRÍTICO (thread separada)
      - Renderiza HUD
    """

    def __init__(self):
        self.source = config.VIDEO_SOURCE
        self.roi = tuple(config.ROI_COORDS) if config.ROI_COORDS else None
        self.detector = FarnebackDetector(roi=self.roi)
        self.notifier = Notifier()
        self.esp32 = ESP32Client()
        self.mock_mode = "normal"  # modo da câmera simulada (normal|alerta|critico)
        self.temp_sensor = TemperatureSensor(mock_mode=self.mock_mode)

        # Thresholds e debounce
        self.alert_th = config.ALERT_THRESHOLD
        self.critical_th = config.CRITICAL_THRESHOLD
        self.alert_debounce = config.ALERT_DEBOUNCE_FRAMES
        self.critical_debounce = config.CRITICAL_DEBOUNCE_FRAMES

        # Temperatura thresholds
        self.temp_max_alerta = config.TEMP_MAX_ALERTA
        self.temp_max_critico = config.TEMP_MAX_CRITICO

        # Aprendizado do padrão da máquina (não supervisionado, sem rótulos)
        self.pattern_enabled = config.PATTERN_LEARNING_ENABLED
        self.pattern = pattern_learner if self.pattern_enabled else None
        self.pattern_ready = bool(self.pattern and self.pattern.status().get("trained"))
        self.pattern_level = "NORMAL"
        self.pattern_score = 0.0
        self.pattern_distance = 0.0
        self.pattern_regime = -1
        self.learning_seconds = config.PATTERN_LEARNING_SECONDS
        window = self.pattern.window if self.pattern else config.PATTERN_WINDOW
        # 4 janelas de histórico (~60 s a 4 Hz) para pontuar e adaptar
        self._pattern_samples: deque = deque(maxlen=max(window * 4, window))
        self._pattern_alert_evals = 0
        self._pattern_critical_evals = 0
        self._last_pattern_sample = 0.0
        self._last_pattern_eval = 0.0
        self._last_pattern_adapt = 0.0
        self._pattern_thread: Optional[threading.Thread] = None

        # Estado
        self.state = State.NORMAL
        self.alert_counter = 0
        self.critical_counter = 0
        self.current_magnitude = 0.0
        self.current_temperature = 0.0
        self.current_temperature_source = "temp_sensor"
        self.fps = 0.0
        self.frame_count = 0
        self.hud_enabled = config.SHOW_HUD
        self._sequence = 0
        self._sidecar: Optional[SidecarTelemetry] = None
        if isinstance(self.source, str) and Path(self.source).exists():
            self._sidecar = SidecarTelemetry(self.source)
            if not self._sidecar.available:
                self._sidecar = None

        # Captura
        self.cap: Optional[cv2.VideoCapture] = None
        self.mock: Optional[MockCamera] = None
        self.mock_mode: str = "normal"  # modo da câmera simulada (normal|alerta|critico)

        # Controle
        self.running = False
        self._last_frame_time = 0.0
        self._last_api_time = 0.0
        self._api_interval = 1.0 / 4  # 4 Hz para API (telemetria suficiente para gráfico)
        self._last_capture_time = 0.0
        self._capture_interval = 2.0  # máx. 1 capture a cada 2s em ALERTA/CRÍTICO

        # Threads
        self._executor = None

    # ---------- Captura ----------
    def _open_source(self):
        if self.source == "mock":
            self.mock = MockCamera()
            self.mock.set_mode(self.mock_mode)
            logger.info(f"MockCamera iniciada (modo simulação: {self.mock.mode}).")
            return
        try:
            self.cap = cv2.VideoCapture(self.source)
            try:
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # minimiza frames acumulados (delay)
            except Exception:
                pass
            if not self.cap.isOpened():
                raise RuntimeError("Falha ao abrir fonte de vídeo.")
            logger.info(f"Fonte de vídeo aberta: {self.source}")
        except Exception as e:
            logger.error(f"Falha ao abrir câmera: {e}")
            raise

    def _reconnect(self):
        logger.warning("Tentando reconectar à fonte de vídeo...")
        if self.cap:
            self.cap.release()
        for attempt in range(5):
            try:
                self.cap = cv2.VideoCapture(self.source)
                try:
                    self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                if self.cap.isOpened():
                    logger.info("Reconexão bem-sucedida.")
                    return True
            except Exception as e:
                logger.error(f"Tentativa {attempt + 1} falhou: {e}")
            time.sleep(2)
        return False

    def _read_frame(self) -> Optional[np.ndarray]:
        if self.mock is not None:
            ok, frame = self.mock.read()
            return frame if ok else None
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        if not ok:
            # Se a fonte for um arquivo de vídeo, faz loop (teste/simulação)
            if isinstance(self.source, str) and Path(self.source).exists():
                logger.info("Fim do arquivo de vídeo — reiniciando para loop de teste.")
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
                if ok:
                    return frame
            if not self._reconnect():
                return None
            ok, frame = self.cap.read()
        return frame if ok else None

    # ---------- Calibração ----------
    def _calibrate(self):
        logger.info(f"Calibrando baseline por {config.CALIBRATION_SECONDS}s...")
        samples = []
        end = time.time() + config.CALIBRATION_SECONDS
        while time.time() < end:
            frame = self._read_frame()
            if frame is None:
                continue
            samples.append(self.detector.process(frame))
            self._render_hud(frame, calibrating=True)
            if self._safe_waitkey(1) == ord("q"):
                return False
        # Auto-ajuste por cena: mediana + MAD da magnitude vista na calibração.
        # O piso continua sendo ALERT_THRESHOLD/CRITICAL_THRESHOLD do .env —
        # cenas ruidosas (webcam de mesa, pouca luz, IR) ganham margem automática.
        usable = np.asarray([s for s in samples if s > 0.0], dtype=np.float64)
        if len(usable) >= 10:
            # Ajusta pela METADE MAIS CALMA das amostras (p25–mediana): se alguém passou
            # na frente da câmera durante os 8s, o fundo parado ainda domina o piso.
            med_all = float(np.median(usable))
            calm = usable[usable <= med_all]
            med = float(np.median(calm)) if len(calm) else med_all
            mad = float(np.median(np.abs(calm - med))) * 1.4826 if len(calm) else 0.0
            # Teto absoluto: nenhum auto-ajuste pode deixar a detecção insensível demais
            new_alert = min(max(self.alert_th, med + 4.0 * mad), 8.0)
            new_crit = min(max(self.critical_th, med + 8.0 * mad), 14.0)
            if new_alert > self.alert_th or new_crit > self.critical_th:
                logger.info(
                    f"Limiares auto-ajustados pela cena: ALERTA {self.alert_th:.2f} → {new_alert:.2f}, "
                    f"CRÍTICO {self.critical_th:.2f} → {new_crit:.2f} (mediana {med:.2f}, MAD {mad:.2f})"
                )
                self.alert_th, self.critical_th = new_alert, new_crit
        logger.info("Calibração concluída. Baseline estabelecida.")
        return True

    # ---------- Aprendizado do padrão (sem rótulos) ----------
    def _learn_pattern_baseline(self):
        """
        Fase de observação: a IA vê a máquina funcionando e aprende sozinha
        o padrão de movimento. Nenhum rótulo, threshold ou diagnóstico é usado.
        """
        if not self.pattern_enabled or self.pattern is None:
            return
        status = self.pattern.status()
        if status.get("trained") and not config.PATTERN_RELEARN_ON_START:
            self.pattern_ready = True
            logger.info(
                "Padrão já aprendido carregado (%s janelas, %s regimes).",
                status.get("n_core"), status.get("n_regimes"),
            )
            return

        seconds = max(5, int(self.learning_seconds))
        interval = 1.0 / max(0.5, self.pattern.sample_hz)
        logger.info(
            "APRENDENDO o padrão da máquina por %ss — apenas observando os "
            "movimentos (sem rótulos).", seconds,
        )
        values: List[float] = []
        times: List[float] = []
        t0 = time.time()
        last_sample = 0.0
        while time.time() - t0 < seconds:
            frame = self._read_frame()
            if frame is None:
                continue
            self._save_latest_frame(frame)
            magnitude = self._detect(frame)
            self.current_magnitude = magnitude
            now = time.time()
            if now - last_sample >= interval:
                last_sample = now
                values.append(float(magnitude))
                times.append(now - t0)
            self._render_hud(frame, learning=True)
            if self._safe_waitkey(1) == ord("q"):
                return

        if len(values) < self.pattern.window + 5:
            logger.warning(
                "Amostras insuficientes (%d) para aprender o padrão; "
                "aumente PATTERN_LEARNING_SECONDS.", len(values),
            )
            return

        report = self.pattern.learn(
            values, times,
            extra_meta={"source": str(self.source), "live_learning_seconds": seconds},
        )
        if not report.get("ok"):
            logger.warning("Aprendizado do padrão falhou: %s", report.get("reason"))
            return

        self.pattern_ready = True
        self._pattern_samples.clear()
        th = report.get("thresholds", {})
        logger.info(
            "PADRÃO APRENDIDO: %s janelas (%s no padrão), %s regimes, "
            "z-alerta>=%.2f | z-crítico>=%.2f",
            report.get("n_windows"), report.get("n_core"),
            report.get("n_regimes"),
            th.get("rms_z_alert", 0.0), th.get("rms_z_critical", 0.0),
        )

    def _maybe_score_pattern(self, magnitude: float):
        """Compara o movimento atual com o padrão aprendido (a cada N segundos)."""
        if not self.pattern_enabled or self.pattern is None or not self.pattern_ready:
            return

        now = time.time()
        sample_interval = 1.0 / max(0.5, self.pattern.sample_hz)
        if now - self._last_pattern_sample >= sample_interval:
            self._last_pattern_sample = now
            self._pattern_samples.append(float(magnitude))

        if len(self._pattern_samples) < self.pattern.window:
            return
        if now - self._last_pattern_eval < max(0.05, config.PATTERN_EVAL_SECONDS):
            return
        self._last_pattern_eval = now

        try:
            result = self.pattern.score(list(self._pattern_samples)[-self.pattern.window:])
        except Exception as e:
            logger.debug(f"Falha ao pontuar padrão de movimento: {e}")
            return
        if not result.get("trained"):
            return

        prev = self.pattern_level
        self.pattern_level = result.get("level", "NORMAL")
        self.pattern_score = float(result.get("anomaly_index", 0.0))
        self.pattern_distance = float(result.get("distance", 0.0))
        self.pattern_regime = int(result.get("regime", -1))

        if self.pattern_level == "CRITICO":
            self._pattern_critical_evals += 1
            self._pattern_alert_evals += 1
        elif self.pattern_level == "ALERTA":
            self._pattern_alert_evals += 1
            self._pattern_critical_evals = 0
        else:
            self._pattern_alert_evals = 0
            self._pattern_critical_evals = 0

        if self.pattern_level != prev:
            logger.info(
                "Padrão de movimento: %s -> %s (anomalia=%.2f, dist=%.3f, regime=%s)",
                prev, self.pattern_level, self.pattern_score,
                self.pattern_distance, self.pattern_regime,
            )

        # Adaptação lenta: só incorpora comportamento NORMAL, com intervalo mínimo
        if (config.PATTERN_ADAPT_ENABLED and self.pattern_level == "NORMAL"
                and now - self._last_pattern_adapt >= config.PATTERN_ADAPT_INTERVAL_SECONDS):
            self._last_pattern_adapt = now
            raw = list(self._pattern_samples)
            window = self.pattern.window
            step = max(1, window // 2)
            for start in range(0, max(1, len(raw) - window + 1), step):
                self.pattern.add_normal_window(raw[start:start + window])
            if self._pattern_thread is None or not self._pattern_thread.is_alive():
                self._pattern_thread = threading.Thread(
                    target=self._refit_pattern, daemon=True
                )
                self._pattern_thread.start()

    def _refit_pattern(self):
        """Reaprende incorporando o drift normal (executa em thread separada)."""
        try:
            result = self.pattern.refit_from_adapt()
            if result.get("ok"):
                logger.info(
                    "Padrão adaptado ao comportamento normal recente: "
                    "%s janelas, z-alerta>=%.2f | z-crítico>=%.2f",
                    result.get("n_core"),
                    result.get("thresholds", {}).get("rms_z_alert", 0.0),
                    result.get("thresholds", {}).get("rms_z_critical", 0.0),
                )
        except Exception as e:
            logger.warning(f"Falha ao adaptar o padrão: {e}")

    # ---------- Detecção ----------
    def _detect(self, frame: np.ndarray) -> float:
        return self.detector.process(frame)

    def _update_state(self, magnitude: float, temperature: float):
        """Máquina de estados com debounce (vibração + temperatura + padrão)."""
        pattern_alert = (
            config.PATTERN_ESCALATE_ALERTA
            and self.pattern_level in (State.ALERTA, State.CRITICO)
            and self._pattern_alert_evals >= config.PATTERN_ALERT_EVALS
        )
        pattern_critical = (
            config.PATTERN_ESCALATE_CRITICO
            and self.pattern_level == State.CRITICO
            and self._pattern_critical_evals >= config.PATTERN_CRITICAL_EVALS
        )

        # Contadores de debounce — crítico vence sobre alerta
        if magnitude >= self.critical_th or temperature >= self.temp_max_critico or pattern_critical:
            self.critical_counter += 1
            self.alert_counter += 1
        elif magnitude >= self.alert_th or temperature >= self.temp_max_alerta or pattern_alert:
            self.alert_counter += 1
            self.critical_counter = 0
        else:
            self.alert_counter = 0
            self.critical_counter = 0

        # Transições
        prev_state = self.state
        if self.critical_counter >= self.critical_debounce:
            self.state = State.CRITICO
        elif self.alert_counter >= self.alert_debounce:
            self.state = State.ALERTA
        else:
            self.state = State.NORMAL

        # Ação em CRÍTICO (vibração, temperatura ou padrão → parada de emergência)
        if self.state == State.CRITICO and prev_state != State.CRITICO:
            trigger_reason = []
            if temperature >= self.temp_max_critico:
                trigger_reason.append(f"temperatura={temperature:.1f}°C")
            if magnitude >= self.critical_th:
                trigger_reason.append(f"magnitude={magnitude:.3f}")
            if pattern_critical:
                trigger_reason.append(f"padrao={self.pattern_score:.2f}")
            logger.critical(f"ESTADO CRÍTICO! {' '.join(trigger_reason)}")
            threading.Thread(target=self._trigger_esp32_stop, daemon=True).start()

        if self.state != prev_state:
            logger.info(
                f"Transição: {prev_state} -> {self.state} "
                f"(mag={magnitude:.3f}, temp={temperature:.1f}°C, "
                f"padrao={self.pattern_level}:{self.pattern_score:.2f})"
            )

    # ---------- Snapshot (frame ao vivo p/ API /site) ----------
    def _save_latest_frame(self, frame: np.ndarray):
        """
        Salva o frame atual em CAPTURES_DIR/latest.jpg (escrita atômica via .tmp + os.replace).
        A API serve esse arquivo em GET /snapshot e GET /stream.
        Limitado a ~20 Hz; reduz para 960px de largura para codificar/transferir rápido.
        """
        now = time.time()
        if now - getattr(self, "_last_snapshot_time", 0.0) < 0.05:
            return
        self._last_snapshot_time = now
        try:
            CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CAPTURES_DIR / "latest.jpg.tmp"
            final = CAPTURES_DIR / "latest.jpg"
            h, w = frame.shape[:2]
            if w > 960:
                scale = 960.0 / w
                frame = cv2.resize(frame, (960, int(h * scale)), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 76])
            if not ok:
                return
            tmp.write_bytes(buf.tobytes())
            os.replace(tmp, final)
        except Exception as e:
            logger.debug(f"Falha ao salvar latest.jpg: {e}")

    # ---------- Estado de movimento (localização) ----------
    def _write_motion_state(self):
        """Grava logs/motion_state.json (atômico) — a API serve em GET /motion."""
        det = self.detector
        if getattr(det, "last_grid", None) is None:
            return
        now = time.time()
        if now - getattr(self, "_last_motion_write", 0.0) < 0.25:
            return
        self._last_motion_write = now
        try:
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": self.state,
                "magnitude": round(float(self.current_magnitude), 3),
                "region": det.last_region,
                "direction": det.last_direction,
                "hot_cell": {"row": det.last_hot[0], "col": det.last_hot[1]},
                "hot_magnitude": round(float(det.last_hot_mag), 3),
                "angle_deg": round(float(det.last_angle_deg), 1),
                "grid": [[round(float(v), 3) for v in row] for row in det.last_grid],
                "temperature": round(float(self.current_temperature), 2),
            }
            tmp = LOGS_DIR / "motion_state.json.tmp"
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, LOGS_DIR / "motion_state.json")
        except Exception as e:
            logger.debug(f"Falha ao gravar motion_state.json: {e}")

    # ---------- Ações ----------
    def _save_capture(self, frame: np.ndarray, status: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{status}_{ts}.jpg"
        filepath = CAPTURES_DIR / filename
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(filepath), frame)
        return str(filepath)

    def _send_to_api(self, status: str, value: float, frame_path: str):
        """Envia evento para API em thread separada.

        Se config.API_BASE_URL estiver definido (ex.: https://evolution-xxx.vercel.app),
        publica direto lá (nuvem); senão, usa API_HOST/API_PORT local.
        """
        try:
            api_host = config.API_HOST if config.API_HOST != "0.0.0.0" else "127.0.0.1"
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "value": round(value, 4),
                "temperature": round(self.current_temperature, 2),
                "frame_path": frame_path,
                "source": "vision",
                "sequence": self._sequence,
            }
            base = (getattr(config, "API_BASE_URL", "") or "").rstrip("/")
            url = f"{base}/events" if base else f"http://{api_host}:{config.API_PORT}/events"
            requests.post(url, json=payload, timeout=5)
            logger.debug(f"Evento enviado à API: {status}")
        except Exception as e:
            logger.error(f"Falha ao enviar evento à API: {e}")

    def _notify(self, status: str, value: float, frame_path: str):
        """Dispara notificação em thread separada."""
        self.notifier.send(status, value, frame_path)

    def _trigger_esp32_stop(self):
        """Comando ao ESP32 em thread separada (com cooldown para não spammar o relé)."""
        now = time.time()
        if now - getattr(self, "_esp32_stop_last", 0.0) < getattr(config, "ESP32_STOP_COOLDOWN_SECONDS", 5):
            logger.debug("ESP32 emergency-stop em cooldown, ignorando comando.")
            return
        self._esp32_stop_last = now
        result = self.esp32.emergency_stop()
        logger.info(f"ESP32 emergency-stop: {result}")

    # ---------- HUD ----------
    def _render_hud(self, frame: np.ndarray, calibrating: bool = False, learning: bool = False):
        if not self.hud_enabled:
            return
        try:
            h, w = frame.shape[:2]
            # ROI
            if self.roi:
                x, y, rw, rh = self.roi
                cv2.rectangle(frame, (x, y), (x + rw, y + rh), (0, 255, 0), 2)
                cv2.putText(frame, "ROI", (x + 5, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # Grade 3x3 de movimento + célula quente + direção
            det = self.detector
            if getattr(det, "last_grid", None) is not None:
                gx0, gy0 = (self.roi[0], self.roi[1]) if self.roi else (0, 0)
                gw = self.roi[2] if self.roi else w
                gh = self.roi[3] if self.roi else h
                cw, ch = gw // 3, gh // 3
                grid_color = (0, 200, 255)
                for i in range(1, 3):
                    cv2.line(frame, (gx0 + i * cw, gy0), (gx0 + i * cw, gy0 + gh), grid_color, 1)
                    cv2.line(frame, (gx0, gy0 + i * ch), (gx0 + gw, gy0 + i * ch), grid_color, 1)
                r, c = det.last_hot
                hx0, hy0 = gx0 + c * cw, gy0 + r * ch
                hx1 = gx0 + (c + 1) * cw if c < 2 else gx0 + gw
                hy1 = gy0 + (r + 1) * ch if r < 2 else gy0 + gh
                hot_color = (0, 255, 255) if det.last_hot_mag < self.alert_th else (0, 0, 255)
                cv2.rectangle(frame, (hx0, hy0), (hx1, hy1), hot_color, 2)
                cxx, cyy = (hx0 + hx1) // 2, (hy0 + hy1) // 2
                ang = np.radians(det.last_angle_deg)
                length = int(min(cw, ch) * 0.4) + int(min(2 * min(cw, ch), det.last_hot_mag * 8))
                dx, dy = int(np.cos(ang) * length), int(np.sin(ang) * length)
                cv2.arrowedLine(frame, (cxx, cyy), (cxx + dx, cyy + dy), (0, 165, 255), 3, tipLength=0.35)
                cv2.putText(frame, f"MOV: {det.last_region} {det.last_direction}",
                            (gx0 + 5, gy0 + gh - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, hot_color, 2)

            # Status color
            color = {"NORMAL": (0, 255, 0), "ALERTA": (0, 255, 255), "CRITICO": (0, 0, 255)}[self.state]

            # Texto
            lines = [
                f"FPS: {self.fps:.1f}",
                f"Magnitude: {self.current_magnitude:.3f}",
                f"Temperature: {self.current_temperature:.1f}°C ({self.current_temperature_source})",
                f"Status: {self.state}",
                f"Movimento: {det.last_region} {det.last_direction}" if getattr(det, "last_grid", None) is not None else "Movimento: --",
                f"Alert Th: {self.alert_th:.2f} | Crit Th: {self.critical_th:.2f}",
            ]
            if calibrating:
                lines.append("CALIBRANDO... aguarde")
            if self.pattern_enabled:
                if learning:
                    lines.append("PADRAO: APRENDENDO (observando)...")
                elif not self.pattern_ready:
                    lines.append("PADRAO: nao aprendido")
                else:
                    lines.append(
                        f"Padrao: {self.pattern_level} "
                        f"(anomalia {self.pattern_score:.2f}, regime {self.pattern_regime})"
                    )

            for i, line in enumerate(lines):
                cv2.putText(frame, line, (10, 30 + i * 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            # Barra de status no topo
            cv2.rectangle(frame, (0, 0), (w, 5), color, -1)

            cv2.imshow("Vision Monitor - Industrial E-Stop", frame)
        except cv2.error as e:
            # Ambiente headless (ex: Codespaces): desativa HUD
            logger.warning(f"HUD indisponível (ambiente headless?): {e}. Desativando HUD.")
            self.hud_enabled = False
            return

    # ---------- Teclado (tolerante a headless) ----------
    def _safe_waitkey(self, delay_ms: int = 1) -> int:
        try:
            key = cv2.waitKey(delay_ms)
            if key == -1:
                return -1
            return key & 0xFF
        except cv2.error:
            # Headless: sem janela e sem teclado
            return -1

    # ---------- FPS ----------
    def _update_fps(self):
        now = time.time()
        if self._last_frame_time > 0:
            dt = now - self._last_frame_time
            if dt > 0:
                self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt)
        self._last_frame_time = now

    # ---------- Loop principal ----------
    def start(self):
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        self._open_source()
        if not self._calibrate():
            return

        # Observa a máquina e aprende o padrão de movimento (sem rótulos)
        self._learn_pattern_baseline()

        self.running = True
        logger.info("Loop principal iniciado.")

        while self.running:
            frame = self._read_frame()
            if frame is None:
                logger.warning("Frame nulo, pulando...")
                continue

            self.frame_count += 1
            self._save_latest_frame(frame)
            magnitude = self._detect(frame)
            self.current_magnitude = magnitude

            # Leitura de temperatura (sidecar do vídeo sincronizado > temp_sensor)
            sidecar_temp = None
            if self._sidecar is not None and self.cap is not None:
                frame_idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
                sidecar_temp = self._sidecar.temperature_at(frame_idx)
            if sidecar_temp is not None:
                temperature = sidecar_temp
                self.current_temperature_source = "sidecar"
            else:
                temperature = self.temp_sensor.read()
                self.current_temperature_source = "temp_sensor"
            self.current_temperature = temperature

            # Compara com o padrão aprendido antes de decidir o estado
            self._maybe_score_pattern(magnitude)

            self._update_state(magnitude, temperature)
            self._write_motion_state()

            now = time.time()

            # Salva capture em ALERTA ou CRÍTICO (com cooldown para não inundar o disco)
            frame_path = None
            if config.EVIDENCE_CAPTURE_ENABLED and self.state in (State.ALERTA, State.CRITICO):
                if now - self._last_capture_time >= self._capture_interval:
                    self._last_capture_time = now
                    frame_path = self._save_capture(frame, self.state)

            # Envio à API (rate-limited a 4Hz)
            if now - self._last_api_time >= self._api_interval:
                self._last_api_time = now
                self._sequence += 1
                threading.Thread(
                    target=self._send_to_api,
                    args=(self.state, magnitude, frame_path or ""),
                    daemon=True,
                ).start()

            # Notificação
            if self.state in (State.ALERTA, State.CRITICO):
                threading.Thread(
                    target=self._notify,
                    args=(self.state, magnitude, frame_path),
                    daemon=True,
                ).start()

            # HUD
            self._update_fps()
            self._render_hud(frame)

            # FPS control
            if self._safe_waitkey(1) == ord("q"):
                break

            # Alvo de FPS
            target_dt = 1.0 / config.TARGET_FPS
            elapsed = time.time() - self._last_frame_time
            if elapsed < target_dt:
                time.sleep(target_dt - elapsed)

        self.stop()

    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()
        if self.mock:
            self.mock.release()
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            logger.info("destroyAllWindows ignorado (ambiente headless).")
        logger.info("VisionMonitor parado.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Vision Monitor Industrial")
    parser.add_argument("--mock", action="store_true", help="Usa câmera simulada (sem hardware)")
    parser.add_argument("--mode", default="normal", choices=["normal", "alerta", "critico"],
                        help="Modo da câmera simulada")
    parser.add_argument("--no-pattern", action="store_true",
                        help="Desliga o aprendizado do padrão de movimento")
    parser.add_argument("--learn-seconds", type=int, default=None,
                        help="Sobrescreve PATTERN_LEARNING_SECONDS na inicialização")
    args = parser.parse_args()

    monitor = VisionMonitor()
    if args.no_pattern:
        monitor.pattern_enabled = False
        monitor.pattern = None
        monitor.pattern_ready = False
        logger.info("Aprendizado do padrão desativado por --no-pattern.")
    if args.learn_seconds is not None:
        monitor.learning_seconds = int(args.learn_seconds)
    if args.mock:
        monitor.source = "mock"
        monitor.mock_mode = args.mode
        logger.info(f"Mock camera em modo: {args.mode}")
    try:
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
        logger.info("Encerrado pelo usuário.")