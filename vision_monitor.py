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
from datetime import datetime
from typing import Optional, List, Tuple, Dict, Any
from collections import deque

from config import config

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

    def _to_gray(self, frame: np.ndarray) -> np.ndarray:
        if len(frame.shape) == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return frame

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

    def process(self, frame: np.ndarray) -> float:
        """
        Processa um frame e retorna o índice de vibração (magnitude média do fluxo).
        No primeiro frame, apenas armazena baseline e retorna 0.0.
        """
        gray = self._to_gray(frame)
        roi_gray = self._crop_roi(gray)

        # Filtro gaussiano para reduzir ruído
        roi_gray = cv2.GaussianBlur(roi_gray, (5, 5), 1.0)

        if self.prev_gray is None:
            self.prev_gray = roi_gray
            return 0.0

        # Garante mesma resolução
        if self.prev_gray.shape != roi_gray.shape:
            self.prev_gray = cv2.resize(self.prev_gray, (roi_gray.shape[1], roi_gray.shape[0]))

        flow = self.compute_flow(self.prev_gray, roi_gray)

        # Magnitude do fluxo
        mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)

        # Índice robusto de vibração: percentil 99 da magnitude.
        # Foca na região em movimento dominante (punha/mancais) e é
        # insensível ao fundo estático e ao ruído de sensor.
        index = float(np.percentile(mag, 99))

        # Atualiza baseline
        self.prev_gray = roi_gray
        return index


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
        now = datetime.utcnow()
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

        # Thresholds e debounce
        self.alert_th = config.ALERT_THRESHOLD
        self.critical_th = config.CRITICAL_THRESHOLD
        self.alert_debounce = config.ALERT_DEBOUNCE_FRAMES
        self.critical_debounce = config.CRITICAL_DEBOUNCE_FRAMES

        # Estado
        self.state = State.NORMAL
        self.alert_counter = 0
        self.critical_counter = 0
        self.current_magnitude = 0.0
        self.fps = 0.0
        self.frame_count = 0
        self.hud_enabled = config.SHOW_HUD

        # Captura
        self.cap: Optional[cv2.VideoCapture] = None
        self.mock: Optional[MockCamera] = None
        self.mock_mode: str = "normal"  # modo da câmera simulada (normal|alerta|critico)

        # Controle
        self.running = False
        self._last_frame_time = 0.0
        self._last_api_time = 0.0
        self._api_interval = 1.0 / 10  # 10 Hz para API (não sobrecarregar)

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
            if not self._reconnect():
                return None
            ok, frame = self.cap.read()
        return frame if ok else None

    # ---------- Calibração ----------
    def _calibrate(self):
        logger.info(f"Calibrando baseline por {config.CALIBRATION_SECONDS}s...")
        end = time.time() + config.CALIBRATION_SECONDS
        while time.time() < end:
            frame = self._read_frame()
            if frame is None:
                continue
            self.detector.process(frame)
            self._render_hud(frame, calibrating=True)
            if self._safe_waitkey(1) == ord("q"):
                return False
        logger.info("Calibração concluída. Baseline estabelecida.")
        return True

    # ---------- Detecção ----------
    def _detect(self, frame: np.ndarray) -> float:
        return self.detector.process(frame)

    def _update_state(self, magnitude: float):
        """Máquina de estados com debounce."""
        # Contadores de debounce
        if magnitude >= self.critical_th:
            self.critical_counter += 1
            self.alert_counter += 1
        elif magnitude >= self.alert_th:
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

        # Ação em CRÍTICO
        if self.state == State.CRITICO and prev_state != State.CRITICO:
            logger.critical(f"ESTADO CRÍTICO! magnitude={magnitude:.3f}")
            threading.Thread(target=self._trigger_esp32_stop, daemon=True).start()

        if self.state != prev_state:
            logger.info(f"Transição: {prev_state} -> {self.state} (mag={magnitude:.3f})")

    # ---------- Snapshot (frame ao vivo p/ API /site) ----------
    def _save_latest_frame(self, frame: np.ndarray):
        """
        Salva o frame atual em CAPTURES_DIR/latest.jpg (escrita atômica via .tmp + os.replace).
        A API serve esse arquivo em GET /snapshot e GET /stream.
        Limitado a ~5 Hz para não thrash de disco.
        """
        now = time.time()
        if now - getattr(self, "_last_snapshot_time", 0.0) < 0.2:
            return
        self._last_snapshot_time = now
        try:
            CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CAPTURES_DIR / "latest.jpg.tmp"
            final = CAPTURES_DIR / "latest.jpg"
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                return
            tmp.write_bytes(buf.tobytes())
            os.replace(tmp, final)
        except Exception as e:
            logger.debug(f"Falha ao salvar latest.jpg: {e}")

    # ---------- Ações ----------
    def _save_capture(self, frame: np.ndarray, status: str) -> str:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{status}_{ts}.jpg"
        filepath = CAPTURES_DIR / filename
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(filepath), frame)
        return str(filepath)

    def _send_to_api(self, status: str, value: float, frame_path: str):
        """Envia evento para API em thread separada."""
        try:
            payload = {
                "timestamp": datetime.utcnow().isoformat(),
                "status": status,
                "value": round(value, 4),
                "frame_path": frame_path,
                "source": "vision",
            }
            url = f"http://{config.API_HOST}:{config.API_PORT}/events"
            requests.post(url, json=payload, timeout=5)
            logger.debug(f"Evento enviado à API: {status}")
        except Exception as e:
            logger.error(f"Falha ao enviar evento à API: {e}")

    def _notify(self, status: str, value: float, frame_path: str):
        """Dispara notificação em thread separada."""
        self.notifier.send(status, value, frame_path)

    def _trigger_esp32_stop(self):
        """Comando ao ESP32 em thread separada."""
        result = self.esp32.emergency_stop()
        logger.info(f"ESP32 emergency-stop: {result}")

    # ---------- HUD ----------
    def _render_hud(self, frame: np.ndarray, calibrating: bool = False):
        if not self.hud_enabled:
            return
        try:
            h, w = frame.shape[:2]
            # ROI
            if self.roi:
                x, y, rw, rh = self.roi
                cv2.rectangle(frame, (x, y), (x + rw, y + rh), (0, 255, 0), 2)
                cv2.putText(frame, "ROI", (x + 5, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # Status color
            color = {"NORMAL": (0, 255, 0), "ALERTA": (0, 255, 255), "CRITICO": (0, 0, 255)}[self.state]

            # Texto
            lines = [
                f"FPS: {self.fps:.1f}",
                f"Magnitude: {self.current_magnitude:.3f}",
                f"Status: {self.state}",
                f"Alert Th: {self.alert_th:.2f} | Crit Th: {self.critical_th:.2f}",
            ]
            if calibrating:
                lines.append("CALIBRANDO... aguarde")

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
            self._update_state(magnitude)

            # Salva capture em ALERTA ou CRÍTICO
            frame_path = None
            if self.state in (State.ALERTA, State.CRITICO):
                frame_path = self._save_capture(frame, self.state)

            # Envio à API (rate-limited a 10Hz)
            now = time.time()
            if now - self._last_api_time >= self._api_interval:
                self._last_api_time = now
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
    args = parser.parse_args()

    monitor = VisionMonitor()
    if args.mock:
        monitor.source = "mock"
        monitor.mock_mode = args.mode
        logger.info(f"Mock camera em modo: {args.mode}")
    try:
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
        logger.info("Encerrado pelo usuário.")