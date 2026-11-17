import sys, time
from harness import BASE, check_usage
ep = str(BASE / "S40_real" / "proj" / "main.py")
for pkg in sys.argv[1:]:
    t = time.time()
    r = check_usage(ep, pkg)
    print(f"check {pkg}: {time.time()-t:.1f}s verdict={r.get('verdict')} chains={len(r.get('import_chains', []))}", flush=True)
