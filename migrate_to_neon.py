"""Migração única: SQLite local (evolution.db) → Postgres (Neon).

Uso (na raiz do projeto, com o venv ativo):
    set MIGRATE_DATABASE_URL=postgresql://user:pass@ep-xxx.neon.tech/dbname?sslmode=require
    python migrate_to_neon.py

O schema no destino é criado a partir dos MESMOS modelos do api.py (create_all).
Os ids são preservados e as sequences do Postgres são reajustadas ao final.
"""
import os
import sys

DST_URL = os.environ.get("MIGRATE_DATABASE_URL", "").strip()
if not DST_URL.startswith("postgresql"):
    sys.exit("Defina MIGRATE_DATABASE_URL=postgresql://... antes de rodar (connection string do Neon).")

SRC_URL = os.environ.get("MIGRATE_SOURCE_URL", "sqlite:///evolution.db")
CHUNK = 500

from sqlalchemy import create_engine, inspect, MetaData, Table, text

src_engine = create_engine(SRC_URL)
# Cria o schema no destino usando os modelos da aplicação
os.environ["DATABASE_URL"] = DST_URL
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api  # noqa: F401  (importar cria engine/destino + cria as tabelas via create_all)

dst_engine = api.engine

TABLES = ["events", "system_logs", "users", "diagnostics"]
src_meta = MetaData()
insp = inspect(src_engine)

for table in TABLES:
    if not insp.has_table(table):
        print(f"[skip] tabela {table!r} não existe na origem")
        continue
    src_table = Table(table, src_meta, autoload_with=src_engine)
    with src_engine.connect() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        rows = conn.execute(src_table.select()).mappings().all()

    if total == 0:
        print(f"[ok] {table}: 0 linhas (nada a migrar)")
        continue

    with dst_engine.begin() as dst:
        dst.execute(text(f"DELETE FROM {table}"))  # idempotente: re-rodar sobrescreve
        for i in range(0, len(rows), CHUNK):
            dst.execute(src_table.insert().values([dict(r) for r in rows[i:i + CHUNK]]))
        # Reajusta a sequence do id (preservamos ids da origem)
        dst.execute(text(
            f"SELECT setval(pg_get_serial_sequence(:t, 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1))"
        ), {"t": table})

    with dst_engine.connect() as dst:
        dst_count = dst.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    status = "OK" if dst_count == total else "DIVERGÊNCIA!"
    print(f"[{status}] {table}: {total} origem → {dst_count} destino")

print("Migração concluída.")
