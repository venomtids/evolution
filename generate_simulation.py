"""
generate_simulation.py
Gera um vídeo de teste/simulação para o EVO-VISION.

Cena: máquina sintética com peça rotativa/vibratória + display térmico (câmera térmica simulada).

Uso:
    python generate_simulation.py
    python generate_simulation.py --output captures/simulation.mp4 --duration 30 --fps 30

Cenário progressivo (default):
    NORMAL 0s–12s  →  ALERTA 12s–22s  →  CRITICO 22s–30s

Depois de gerar, use o vídeo como fonte de teste no monitor:
    VIDEO_SOURCE=captures/simulation.mp4 python vision_monitor.py
"""

import argparse
import json
import math
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


def _pseudo_noise(t: float, freq: float = 37.7) -> float:
    """Ruído determinístico (sem estado) — mesmo t => mesmo valor no sidecar e no frame."""
    return float(np.sin(t * freq) * 0.5 + np.sin(t * 13.3 + 1.7) * 0.3)


def temperature_at(t: float, phase: str) -> float:
    """Modelo térmico canônico (idêntico no frame e no sidecar)."""
    if phase == "normal":
        return 38.0 + float(np.sin(t * 0.5)) * 1.5
    if phase == "alerta":
        return 63.0 + float(np.sin(t * 1.2)) * 3.0
    # critico: 75–83 °C (supera TEMP_MAX_CRITICO=75 na maioria dos frames)
    return 79.0 + float(np.sin(t * 2.0)) * 4.0 + _pseudo_noise(t)


def rpm_at(t: float, phase: str) -> float:
    if phase == "normal":
        return 1500.0 + float(np.sin(t * 0.5)) * 8.0
    if phase == "alerta":
        return 1800.0 + float(np.sin(t * 6.0)) * 120.0
    return 2350.0 + float(np.sin(t * 10.0)) * 450.0 + _pseudo_noise(t, 29.1) * 60.0


def render_frame(t: float, phase: str,
                 width: int = 640, height: int = 480) -> np.ndarray:
    """Renderiza um frame da cena sintética."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (40, 40, 40)

    # ---- Fundo: chão de fábrica ----
    cv2.rectangle(img, (0, height - 60), (width, height), (35, 35, 40), -1)
    cv2.line(img, (0, height - 60), (width, height - 60), (80, 80, 90), 2)

    # ---- Máquina (corpo fixo) ----
    machine_x, machine_y, machine_w, machine_h = 80, 100, 220, 300
    cv2.rectangle(img, (machine_x, machine_y), (machine_x + machine_w, machine_y + machine_h), (70, 70, 75), -1)
    cv2.rectangle(img, (machine_x, machine_y), (machine_x + machine_w, machine_y + machine_h), (90, 90, 95), 2)
    cv2.putText(img, "TORNO-CNC 01", (machine_x + 20, machine_y + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    # ---- ROI interna (área isolada) ----
    roi_x, roi_y, roi_w, roi_h = 120, 180, 140, 140
    cv2.rectangle(img, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h), (50, 50, 55), -1)
    cv2.rectangle(img, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h), (0, 255, 0), 2)

    # ---- Temperatura simulada por fase (mesma curva exportada no sidecar) ----
    temp = temperature_at(t, phase)

    # Cor da barra térmica (<45 verde, <75 laranja, acima CRÍTICO vermelho)
    if temp < 45:
        thermo_color = (40, 180, 40)
    elif temp < 75:
        thermo_color = (0, 200, 255)
    else:
        thermo_color = (0, 0, 220)

    # ---- Peça móvel (oscilação configurável) ----
    base_x, base_y = roi_x + roi_w // 2, roi_y + roi_h // 2
    if phase == "normal":
        amp = 1.5
        freq = 0.8
    elif phase == "alerta":
        amp = 8.0
        freq = 6.0
    else:  # critico
        amp = 14.0
        freq = 10.0

    pos = base_x + math.sin(t * freq) * amp, base_y + math.cos(t * freq * 0.9) * amp * 0.7

    # Peça: círculo vermelho com contorno
    cv2.circle(img, (int(pos[0]), int(pos[1])), 35, (0, 0, 255), -1)
    cv2.circle(img, (int(pos[0]), int(pos[1])), 35, (0, 100, 0), 1)
    cv2.circle(img, (int(pos[0]), int(pos[1])), 10, (0, 80, 255), -1)

    # ---- Display térmico (câmera térmica simulada) ----
    thermo_x, thermo_y, thermo_w, thermo_h = 360, 120, 220, 260
    cv2.rectangle(img, (thermo_x, thermo_y), (thermo_x + thermo_w, thermo_y + thermo_h), (55, 55, 60), -1)
    cv2.rectangle(img, (thermo_x, thermo_y), (thermo_x + thermo_w, thermo_y + thermo_h), (100, 100, 105), 2)
    cv2.putText(img, "THERMAL VIEW", (thermo_x + 15, thermo_y + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

    # Barra de temperatura vertical
    bar_x = thermo_x + 40
    bar_y = thermo_y + 50
    bar_h = 160
    bar_w = 40
    cv2.rectangle(img, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (30, 30, 30), -1)
    fill = min(1.0, max(0.0, (temp - 20.0) / 60.0))
    fill_h = int(bar_h * fill)
    cv2.rectangle(img, (bar_x, bar_y + bar_h - fill_h), (bar_x + bar_w, bar_y + bar_h), thermo_color, -1)

    # Valor numérico
    cv2.putText(img, f"{temp:.1f}C", (thermo_x + 95, bar_y + 90),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, thermo_color, 2)

    # Indicador de alerta
    if phase == "alerta":
        cv2.putText(img, "ALERT", (thermo_x + 95, bar_y + 130),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 255), 2)
    elif phase == "critico":
        cv2.putText(img, "CRIT!", (thermo_x + 95, bar_y + 130),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

    # ---- HUD top bar ----
    cv2.rectangle(img, (0, 0), (width, 5), thermo_color, -1)

    # ---- Timestamp + fase ----
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(img, f"Phase: {phase.upper()}", (10, height - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
    cv2.putText(img, now_str, (width - 200, height - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

    return img


def generate_scenario(output: Path, duration: float, fps: int = 30,
                      mode: str = "progressive") -> None:
    width, height = 640, 480
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output), fourcc, fps, (width, height))

    sidecar_path = output.with_suffix(".telemetry.jsonl")
    sidecar = open(sidecar_path, "w", encoding="utf-8")

    total_frames = int(duration * fps)
    if mode == "normal":
        phases = [("normal", total_frames)]
    elif mode == "alerta":
        phases = [("alerta", total_frames)]
    elif mode == "critico":
        phases = [("critico", total_frames)]
    else:  # progressive
        n_normal = int(total_frames * 0.40)
        n_alert = int(total_frames * 0.33)
        n_crit = total_frames - n_normal - n_alert
        phases = [("normal", n_normal), ("alerta", n_alert), ("critico", n_crit)]

    t = 0.0
    dt = 1.0 / fps
    frame_idx = 0
    for phase, count in phases:
        for _ in range(count):
            temp = temperature_at(t, phase)
            rpm = rpm_at(t, phase)
            frame = render_frame(t, phase, width, height)
            writer.write(frame)
            sidecar.write(json.dumps({
                "frame": frame_idx,
                "seconds": round(t, 3),
                "phase": phase,
                "temperature": round(temp, 2),
                "rpm": round(rpm, 1),
            }) + "\n")
            t += dt
            frame_idx += 1

    writer.release()
    sidecar.close()
    print(f"Vídeo gerado: {output}")
    print(f"  Sidecar:      {sidecar_path}")
    print(f"  Frames: {frame_idx} | Duração: {duration}s | FPS: {fps}")
    print(f"  Use no monitor: VIDEO_SOURCE={output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera vídeo de simulação para o EVO-VISION")
    parser.add_argument("--output", default="captures/simulation.mp4",
                        help="Caminho de saída do vídeo (padrão: captures/simulation.mp4)")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Duração em segundos (padrão: 30)")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--mode", choices=["progressive", "normal", "alerta", "critico"],
                        default="progressive",
                        help="Cenário: progressive (normal→alerta→critico) ou fase única")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    generate_scenario(output, duration=args.duration, fps=args.fps, mode=args.mode)
