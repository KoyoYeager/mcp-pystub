"""スタブの合計バイト: python stub_size.py <code_root> <scenario> <item> ...（item: pkg か sub=>parent）"""
import subprocess
import sys

C = r'''
import sys
sys.path.insert(0, sys.argv[1])
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs
n, item = sys.argv[2], sys.argv[3]
E = rf"C:\tmp\pystub-e2e\{n}\proj\entry.py"; SP = rf"C:\tmp\pystub-e2e\{n}\venv\Lib\site-packages"
g = generate_submodule_stubs(E, item.split("=>")[1], item.split("=>")[0], SP) if "=>" in item else generate_stubs(E, item, SP)
print(len(g["files"]), sum(len(c.encode()) for c in g["files"].values()))
'''
root, n = sys.argv[1], sys.argv[2]
for item in sys.argv[3:]:
    out = subprocess.run([sys.executable, "-c", C, root, n, item], capture_output=True, text=True).stdout.split()
    print(n, item, "files", out[0], "bytes", out[1], f"({int(out[1]) / 1048576:.2f} MB)")
