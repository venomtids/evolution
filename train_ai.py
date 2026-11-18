#!/usr/bin/env python3
"""
train_ai.py
Treina a IA open-source (scikit-learn) com os dados locais ou por bootstrap.

Uso:
    python train_ai.py                    # treina com o SQLite local (evolution.db)
    python train_ai.py --synthetic-only    # ignora o DB; treina só com dados sintéticos
    python train_ai.py --limit 5000        # usa só os últimos N eventos do DB
    python train_ai.py --no-bootstrap      # não completa com dados sintéticos
"""

import argparse
import json
import re
import sys
from pathlib import Path

from config import config
from ml_model import ml_engine


def load_events_from_db(limit: int) -> list:
    """Lê os últimos eventos do SQLite configurado em DATABASE_URL."""
    import sqlite3

    url = config.DATABASE_URL
    m = re.match(r"sqlite:///(.+)", url)
    if not m:
        print(f"[ERRO] DATABASE_URL não é sqlite local: {url}")
        return []
    db_path = Path(m.group(1))
    if not db_path.exists():
        print(f"[AVISO] Banco {db_path} não existe ainda — usando bootstrap sintético.")
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT timestamp, status, value FROM events ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    )
    rows = list(cur.fetchall())
    conn.close()
    # ordem cronológica
    rows.reverse()
    return [{"timestamp": r["timestamp"], "status": r["status"], "value": r["value"]}
            for r in rows]


def main():
    parser = argparse.ArgumentParser(description="Treina a IA open-source para manutenção preditiva")
    parser.add_argument("--synthetic-only", action="store_true",
                        help="Treina apenas com dados sintéticos (ignora o DB)")
    parser.add_argument("--limit", type=int, default=20000,
                        help="Últimos N eventos do DB usados no treino (default 20000)")
    parser.add_argument("--no-bootstrap", action="store_true",
                        help="Não completa dados insuficientes com bootstrap sintético")
    args = parser.parse_args()

    events = [] if args.synthetic_only else load_events_from_db(args.limit)
    print(f"Eventos reais carregados: {len(events)}")

    metrics = ml_engine.train(events, use_synthetic=not args.no_bootstrap)
    print()
    print("=" * 60)
    if not metrics.get("ok"):
        print(f"[FALHOU] {metrics.get('reason')}")
        sys.exit(1)
    print("✅ IA treinada com sucesso!")
    print(f"  Janelas:        {metrics['n_windows']} ({metrics['n_normal_windows']} normais)")
    print(f"  Amostras:       {metrics['n_samples']} ({metrics['n_real_events']} reais + bootstrap {'sim' if metrics['used_synthetic_bootstrap'] else 'não'})")
    print(f"  Features:       {', '.join(metrics['feature_names'])}")
    cls = metrics.get("classifier", {})
    if cls.get("train_accuracy") is not None:
        print(f"  Classificador:  acurácia {cls['train_accuracy']:.1%} | classes: {', '.join(cls.get('classes', []))}")
    print(f"  IsolationForest: contamination={metrics['iso_forest']['contamination']}, n_estimators={metrics['iso_forest']['n_estimators']}")
    print(f"  Tempo de treino: {metrics['train_seconds']}s")
    print(f"  Modelo salvo em: models/ (anomaly_iforest.joblib, status_classifier.joblib, metadata.json)")
    print()
    print("Para usar nos laudos da API: defina AI_PROVIDER=ml no .env")
    print("=" * 60)


if __name__ == "__main__":
    main()
