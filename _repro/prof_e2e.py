"""e2e venv の analyze をプロファイル: python prof_e2e.py 03_networkx"""
import cProfile, io, pstats, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze

ROOT = Path(r"C:\tmp\pystub-e2e")
name = sys.argv[1]
entry = ROOT / name / "proj" / "entry.py"
sp = ROOT / name / "venv" / "Lib" / "site-packages"
pr = cProfile.Profile()
t = time.time()
pr.enable()
analyze(str(entry), python_path=str(sp))
pr.disable()
print("total", round(time.time() - t, 1))
s = io.StringIO()
st = pstats.Stats(pr, stream=s)
st.sort_stats("cumulative").print_stats(30)
st.sort_stats("tottime").print_stats(20)
print(s.getvalue())
