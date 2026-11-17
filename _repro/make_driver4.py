"""driver3.py から driver4.py（after_round3: 証明済み / 証明済み+conditional / 条件を破るエントリー）を作る。"""
from pathlib import Path

D = Path(__file__).resolve().parent
s = (D / "driver3.py").read_text(encoding="utf-8")


def rep(a, b):
    global s
    if s.count(a) != 1:
        raise SystemExit("NOT FOUND: " + a[:70])
    s = s.replace(a, b, 1)


rep('OUT = ROOT / "after_round2"', 'OUT = ROOT / "after_round3"')
rep('"""Round-2 re-measurement (copy of driver2.py; OUT=after_round2).',
    '"""Round-3 re-measurement (proven / proven+conditional / violating entry; OUT=after_round3).')

# 違反エントリー
rep('''ENTRY2 = {''', '''VIOLATE = {
    # Signal.plot() は Qt の画面を開くので元の環境では実行しない（スタブ適用時に例外になることだけ確かめる）
    "12_asammdf": ("""
import numpy as np
from asammdf import Signal
t = np.arange(0, 10, 1.0)
s = Signal(samples=t*2, timestamps=t, name="sig1", unit="V")
s.plot()
print("plotted")
""", False),
    "02_openpyxl": ("""
import openpyxl, os
wb = openpyxl.load_workbook(os.path.join(os.path.dirname(os.path.abspath(__file__)), "with_image.xlsx"))
ws = wb.active
print("images", len(ws._images), ws["A1"].value)
""", True),
}
MAKE_IMAGE_BOOK = """
import io, openpyxl, sys
from PIL import Image as PI
from openpyxl.drawing.image import Image
im = PI.new("RGB", (4, 4), (255, 0, 0)); b = io.BytesIO(); im.save(b, format="PNG"); b.seek(0)
wb = openpyxl.Workbook(); ws = wb.active; ws["A1"] = "hello"
ws.add_image(Image(b), "B2"); wb.save(sys.argv[1])
"""

ENTRY2 = {''')

# 提案の取り出し: 証明済み + 条件付き
rep('''    stub_pkgs = [x["package_name"] for x in A["stubbable"]]
    nof = [x["package_name"] for x in A["nofollow"]]
    save()
    if not (stub_pkgs or nof or hints):''', '''    stub_pkgs = [x["package_name"] for x in A["stubbable"]]
    nof = [x["package_name"] for x in A["nofollow"]]
    cond_pkgs = [x["package_name"] for x in A.get("conditional", [])]
    cond_hints = {}
    for x in A.get("conditional_submodule_hints", []) or []:
        cond_hints[x["submodule"]] = x
    R["conditional"] = [{k: x.get(k) for k in ("package_name", "estimated_savings_mb", "conditions_total", "unproven_because")}
                        | {"conditions": [{k: g.get(k) for k in ("entry", "public_api", "entry_location", "why_unproven", "uses", "may_be_swallowed")}
                                          for g in x.get("conditions", [])]} for x in A.get("conditional", [])]
    R["conditional_hints"] = [{k: h.get(k) for k in ("submodule", "parent_package", "target_package", "estimated_savings_mb", "conditions_total")}
                              | {"conditions": [{k: g.get(k) for k in ("entry", "public_api", "entry_location", "why_unproven", "uses", "may_be_swallowed")}
                                                for g in h.get("conditions", [])]} for h in cond_hints.values()]
    save()
    if not (stub_pkgs or nof or hints or cond_pkgs or cond_hints):'''),
    # 生成
rep('''    for sub, h in hints.items():
        g = gen("sub", h["parent_package"], sub)
        if g:
            items.append((sub, "sub", g))''', '''    for sub, h in hints.items():
        g = gen("sub", h["parent_package"], sub)
        if g:
            items.append((sub, "sub", g))
    cond_keys = []
    for p in cond_pkgs:
        g = gen("pkg", p)
        if g:
            items.append((p, "pkg", g))
            cond_keys.append(p)
    for sub, h in cond_hints.items():
        if sub in hints:
            continue
        g = gen("sub", h["parent_package"], sub)
        if g:
            items.append((sub, "sub", g))
            cond_keys.append(sub)''')

# 変種: 証明済みのみ / 証明済み + 条件付き（違反エントリーも適用中に実行）
rep('''    allkeys = [k for k, _, _ in items]
    pk = [k for k, kd, _ in items if kd == "pkg"]
    sk = [k for k, kd, _ in items if kd == "sub"]
    full = variant("full", allkeys, nof, cycle=bool(allkeys))
    cats = [c for c in (pk, nof, sk) if c]
    if len(cats) > 1:
        if pk:
            variant("stubbable_only", pk, [])
        if nof:
            variant("nofollow_only", [], nof)
        if sk:
            variant("submodule_only", sk, [])
    if full is not None and full.get("match") is not True:
        if len(allkeys) + len(nof) > 10:
            groups = {}
            for k in allkeys:
                groups.setdefault(k.split(".")[0], []).append(k)
            for gname, ks in groups.items():
                variant("group_" + gname, ks, [])
        else:
            for k in allkeys:
                variant("only_" + k.replace(".", "_"), [k], [])
            for x in nof:
                variant("excl_" + x, [], [x])''', '''    proven_keys = [k for k, _, _ in items if k not in cond_keys]
    all_keys = [k for k, _, _ in items]
    if proven_keys or nof:
        variant("proven", proven_keys, nof, cycle=bool(proven_keys))
    if cond_keys:
        variant("proven_plus_conditional", all_keys, nof, cycle=False, violate=True)''')

# variant(): violate=True なら適用中に違反エントリーを実行
rep('''    def variant(tag, keys, excl, cycle=False):''', '''    viol = None
    if name in VIOLATE:
        vsrc, run_orig = VIOLATE[name]
        vdir = D / "proj_violate"
        vdir.mkdir(exist_ok=True)
        vent = vdir / "entry_violate.py"
        vent.write_text(vsrc.strip() + "\\n", encoding="utf-8")
        if name == "02_openpyxl":
            (vdir / "mk.py").write_text(MAKE_IMAGE_BOOK, encoding="utf-8")
            sh([str(VPY), str(vdir / "mk.py"), str(vdir / "with_image.xlsx")], env=env, log=D / "violate_make_book.log")
        vres = {"entry": str(vent)}
        if run_orig:
            rc, o, e, _ = sh([str(VPY), str(vent)], env=env, log=D / "violate_original.log")
            vres["original"] = {"rc": rc, "stdout": o[-2000:], "stderr": e[-3000:]}
        else:
            vres["original"] = "not run (opens a Qt window)"
        viol = (vent, vres)
        R["violate"] = vres
        save()

    def variant(tag, keys, excl, cycle=False, violate=False):'''),
rep('''        b = build(tag, excl)
        v.update(b)''', '''        b = build(tag, excl)
        v.update(b)
        if violate and viol is not None:
            vent, vres = viol
            venv2 = dict(env, QT_QPA_PLATFORM="offscreen")
            rc, o, e, _ = sh([str(VPY), str(vent)], env=venv2, timeout=300, log=D / f"violate_{tag}.log")
            vres["with_stubs"] = {"rc": rc, "stdout": o[-2000:], "stderr": e[-4000:]}
            R["violate"] = vres
            save()''')
(D / "driver4.py").write_text(s, encoding="utf-8")
print("written")
