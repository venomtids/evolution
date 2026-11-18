# Entrypoint do Streamlit Community Cloud (aponte para este arquivo).
# Executa o dashboard real (dashboard.py na raiz) mantendo o bundle da
# Vercel enxuto. Docs: requirements.txt lido no mesmo diretorio.
import pathlib
import runpy
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / "dashboard.py"), run_name="__main__")
