#!/usr/bin/env python3
"""
learn_pattern.py
Treina a IA para reconhecer o PADRÃO da máquina SEM rótulos e SEM informações
prévias: ela apenas observa os movimentos e descobre sozinha o que é normal.

Modos:
    python learn_pattern.py                              # histórico do SQLite (só valores)
    python learn_pattern.py --source mock --seconds 30
    python learn_pattern.py --source 0 --seconds 60      # webcam ao vivo
    python learn_pattern.py --source rtsp://... --seconds 120
    python learn_pattern.py --source captures/simulation.mp4 --seconds 60
    python learn_pattern.py --reset                      # esquece o padrão aprendido

O treino histórico IGNORA a coluna `status` (NORMAL/ALERTA/CRITICO): nenhum
rótulo é usado. O próprio algoritmo descobre o comportamento padrão pela
densidade das janelas de movimento (k-NN), separa regimes normais (KMeans) e
calibra os limites de anomalia pelos percentis do próprio normal.
"""

import argparse
import re
import sys
from pathlib import Path

from config import config
from pattern_learner import pattern_learner, learn_from_history, learn_live


def db_path_from_config() -> str:
    url = config.DATABASE_URL
    m = re.match(r"sqlite:///(.+)", url)
    return m.group(1) if m else url


def _parse_source(raw: str):
    if raw is None:
        return None
    if raw.lower() == "mock":
        return "mock"
    try:
        return int(raw)  # índice de webcam
    except ValueError:
        return raw


def print_report(report: dict) -> None:
    print()
    print("=" * 68)
    if not report.get("ok"):
        print(f"[FALHOU] {report.get('reason')}")
        print("=" * 68)
        return

    print("✅ PADRÃO APRENDIDO (100% não supervisionado — nenhum rótulo usado)")
    print(f"  Observações:     {report.get('n_samples', 0)} amostras de movimento")
    print(f"  Janelas:         {report.get('n_windows', 0)} "
          f"({report.get('n_core', 0)} no padrão, "
          f"{100 * report.get('core_fraction', 0):.0f}%)")
    print(f"  Janela:          {report.get('window')} amostras @ "
          f"{report.get('sample_hz')} Hz")
    print(f"  Regimes normais: {report.get('n_regimes', 1)}")
    for r in report.get("regimes", []):
        print(f"     - regime {r['id']}: {r['windows']} janelas | "
              f"pico típico={r['peak_median']} | média={r['mean_median']}")
    th = report.get("thresholds", {})
    print(f"  Limite ALERTA:   z-score robusto ≥ {th.get('rms_z_alert')} "
          f"(max_z ≥ {th.get('max_z_alert')})")
    print(f"  Limite CRÍTICO:  z-score robusto ≥ {th.get('rms_z_critical')} "
          f"(max_z ≥ {th.get('max_z_critical')})")
    print(f"  Distância k-NN:  mediana {th.get('distance_median')} "
          f"(referência de densidade)")
    print(f"  Algoritmo:       {report.get('algorithm')}")
    print(f"  Tempo de treino: {report.get('train_seconds')}s")
    print(f"  Modelo:          {report.get('model_path')}")
    print()
    print("O Vision Monitor carrega esse padrão automaticamente e compara cada")
    print("janela de movimento com o normal aprendido. Para reaprender ao vivo:")
    print("  python learn_pattern.py --source 0 --seconds 60")
    print("=" * 68)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Aprende o padrão de movimento da máquina sem rótulos"
    )
    parser.add_argument("--source", default=None,
                        help="mock | índice da webcam (0) | URL RTSP | arquivo de vídeo. "
                             "Omitido = aprende do histórico do SQLite")
    parser.add_argument("--seconds", type=float, default=60.0,
                        help="Duração da observação ao vivo (default 60 s)")
    parser.add_argument("--limit", type=int, default=50000,
                        help="Máximo de observações do SQLite (default 50000)")
    parser.add_argument("--db", default=None,
                        help="Caminho do SQLite (default: DATABASE_URL)")
    parser.add_argument("--sample-hz", type=float, default=None,
                        help=f"Taxa de amostragem (default {config.PATTERN_SAMPLE_HZ} Hz)")
    parser.add_argument("--reset", action="store_true",
                        help="Apaga o padrão aprendido e sai")
    args = parser.parse_args()

    if args.reset:
        result = pattern_learner.reset()
        print(f"🧹 Padrão aprendido apagado: {result}")
        return

    if args.source is not None:
        source = _parse_source(args.source)
        print(f"👁️  Observando {source} por {args.seconds:.0f}s "
              f"a {args.sample_hz or config.PATTERN_SAMPLE_HZ} Hz (sem rótulos)...")
        report = learn_live(source, seconds=args.seconds, sample_hz=args.sample_hz)
    else:
        db_path = args.db or db_path_from_config()
        print(f"📚 Aprendendo o padrão com o histórico {db_path} (somente valores)...")
        report = learn_from_history(db_path, limit=args.limit)

    print_report(report)
    if not report.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
