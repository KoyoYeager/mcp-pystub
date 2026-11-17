"""スタブの生成物を 2 つのコードで比べる: python gen_cmp.py <code_root_a> <code_root_b> <scenario> <pkg|sub=>parent> ..."""
import json, subprocess, sys, hashlib
CHILD = r'''
import sys, json, hashlib
sys.path.insert(0, sys.argv[1])
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs
n, item = sys.argv[2], sys.argv[3]
E = rf"C:\tmp\pystub-e2e\{n}\proj\entry.py"; SP = rf"C:\tmp\pystub-e2e\{n}\venv\Lib\site-packages"
if "=>" in item:
    sub, parent = item.split("=>")
    g = generate_submodule_stubs(E, parent, sub, SP)
else:
    g = generate_stubs(E, item, SP)
print(json.dumps({k: hashlib.md5(v.encode()).hexdigest() for k, v in g.get("files", {}).items()}))
'''
a, b, n = sys.argv[1:4]
for item in sys.argv[4:]:
    ra = json.loads(subprocess.run([sys.executable, "-c", CHILD, a, n, item], capture_output=True, text=True).stdout.splitlines()[-1])
    rb = json.loads(subprocess.run([sys.executable, "-c", CHILD, b, n, item], capture_output=True, text=True).stdout.splitlines()[-1])
    diff = sorted(k for k in set(ra) | set(rb) if ra.get(k) != rb.get(k))
    print(n, item, "files", len(ra), len(rb), "same" if not diff else f"DIFF {len(diff)} e.g. {diff[:3]}")
