"""Checker3 harness: orig run -> analyze(main.py) -> apply proven (+conditional) -> run every entry, compare stdout/stderr/rc."""
import sys, os, json, shutil, subprocess, textwrap, importlib.machinery
from pathlib import Path
REPO = str(Path(__file__).resolve().parents[2])  # リポジトリの根
sys.path.insert(0, REPO)
from mcp_pystub.analyzer import analyze  # noqa
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa
EXT = importlib.machinery.EXTENSION_SUFFIXES[0]
import os as _os
BASE = Path(_os.environ.get("PYSTUB_ATTACK_RUNS") or (Path(__file__).resolve().parent / "runs"))
RESULTS = []

def write_tree(root, files):
    for rel, code in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(code, bytes): p.write_bytes(code)
        else: p.write_text(textwrap.dedent(code), encoding="utf-8")

def run_py(paths, entry, strict=False, module=None, cwd=None):
    if module:
        code = "import sys; sys.path[:0]=%r; import runpy; runpy.run_module(%r, run_name='__main__', alter_sys=True)" % (paths, module)
    else:
        code = "import sys; sys.path[:0]=%r; import runpy; runpy.run_path(%r, run_name='__main__')" % (paths, entry)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("MCP_PYSTUB_STRICT", None)
    if strict: env["MCP_PYSTUB_STRICT"] = "1"
    try:
        r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace", env=env, cwd=cwd)
    except subprocess.TimeoutExpired as e:
        return "TIMEOUT", (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), "TIMEOUT(20s, killed)"
    return r.returncode, r.stdout.strip(), r.stderr.strip()

def rm(site, name):
    base = site.joinpath(*name.split("."))
    for t in (base, base.with_name(base.name + ".py"), base.with_name(base.name + EXT)):
        if t.is_dir(): shutil.rmtree(t)
        elif t.exists(): t.unlink()

def put(site, files):
    for rel, code in files.items():
        compile(code, rel, "exec")
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")

def last(s, n=2):
    return " | ".join(s.splitlines()[-n:])

def build_site(root, sp, ep, res, level):
    s = root / f"site_{level}"
    if s.exists(): shutil.rmtree(s)
    shutil.copytree(sp, s)
    applied = []
    pk = [p["package_name"] for p in res["stubbable"]]
    hints = [(h["submodule"], h["target_package"]) for h in res.get("submodule_stub_hints", [])]
    if level == "all":
        pk += [p["package_name"] for p in res.get("conditional", [])]
        hints += [(h["submodule"], h["target_package"]) for h in res.get("conditional_submodule_hints", [])]
    for p in pk:
        g = generate_stubs(ep, p, python_path=str(sp))
        if "error" in g:
            applied.append(f"GENERR {p}: {g['error']}"); continue
        rm(s, p); put(s, g["files"]); applied.append(f"stub:{p}" + ("(loud)" if g.get("conditional") else ""))
    for p in [p["package_name"] for p in res["nofollow"]]:
        rm(s, p); applied.append(f"nof:{p}")
    for sub, tgt in hints:
        g = generate_submodule_stubs(ep, sub.split(".")[0], sub, python_path=str(sp))
        if "error" in g:
            applied.append(f"GENERR {sub}: {g['error']}"); continue
        rm(s, sub); put(s, g["files"]); rm(s, tgt); applied.append(f"sub:{sub}-X{tgt}")
    return s, applied

def cscenario(name, sp_files, proj_files, entries=("main.py",), levels=("proven", "all"), note=""):
    """entries[0] is analyzed. Others are 'alternate usages' run against the same applied site."""
    root = BASE / name
    if root.exists(): shutil.rmtree(root)
    sp, proj = root / "sp", root / "proj"
    sp.mkdir(parents=True); proj.mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    write_tree(sp, sp_files); write_tree(proj, proj_files)
    ep = str(proj / entries[0])
    res = analyze(ep, python_path=str(sp), include_small_conditional=True)
    print(f"===== {name} {note}")
    for k in ("stubbable", "nofollow", "required"):
        for p in res[k]:
            print(f"   {k:9s} {p['package_name']}: {p['reason'][:160]}")
    for p in res.get("conditional", []):
        print(f"   COND      {p['package_name']} conf={p['confidence']} total={p['conditions_total']}")
        for c in p["conditions"]:
            print(f"        entry={c['entry']} why={c['why_conditional'][:20]} uses={c['uses']} swallow={c['may_be_swallowed']} proj_uses={c['project_uses_count']} api={c.get('public_api')}")
    for h in res.get("submodule_stub_hints", []):
        print(f"   HINT      {h['submodule']} -X {h['target_package']}")
    for h in res.get("conditional_submodule_hints", []):
        print(f"   CHINT     {h['submodule']} -X {h['target_package']} conf={h['confidence']} total={h['conditions_total']}")
        for c in h["conditions"]:
            print(f"        entry={c['entry']} why={c['why_conditional'][:20]} uses={c['uses']} swallow={c['may_be_swallowed']} proj_uses={c['project_uses_count']}")
    findings = []
    origs = {}
    for e in entries:
        origs[e] = run_py([str(proj), str(sp)], str(proj / e))
        print(f"   orig[{e}] rc={origs[e][0]} out={last(origs[e][1])!r} err={last(origs[e][2],1)!r}")
    for lv in levels:
        if lv == "all" and not (res.get("conditional") or res.get("conditional_submodule_hints")):
            continue
        site, applied = build_site(root, sp, ep, res, lv)
        if not [a for a in applied if not a.startswith("GENERR")] and lv == "proven":
            print(f"   [{lv}] nothing to apply"); continue
        print(f"   [{lv}] applied={applied}")
        for e in entries:
            rc0, o0, e0 = origs[e]
            rc, o, er = run_py([str(proj), str(site)], str(proj / e))
            rcs, os_, ers = run_py([str(proj), str(site)], str(proj / e), strict=True)
            notice = "mcp-pystub" in er
            if rc == rc0 and o == o0 and er == e0:
                kind = "SAME"
            elif rc == rc0 and o == o0:
                kind = "SAME_STDOUT(stderr differs)"
            elif rc != 0 and "PystubConditionalStubError" in er:
                kind = "LOUD"
            elif rc == 0 and o != o0:
                kind = "SILENT_STDOUT_CHANGE" + ("(stderr notice)" if notice else "(NO notice)")
            else:
                kind = "CHANGED_OTHER" + ("(notice)" if notice else "")
            print(f"   [{lv}] run[{e}] {kind} rc={rc} out={last(o)!r} err={last(er,1)!r} | STRICT rc={rcs}")
            if kind != "SAME":
                findings.append((lv, e, kind))
    RESULTS.append((name, findings))
    print(f"RESULT {name} {findings}", flush=True)
    if os.environ.get("PYSTUB_VERIFY_CHECK") == "1" and any(e == entries[0] for _lv, e, _k in findings):
        _verify_check(name, root, sp, proj, ep, res, origs[entries[0]])
    if os.environ.get("PYSTUB_TRACE_CHECK") == "1" and any(e == entries[0] for _lv, e, _k in findings):
        _trace_check(name, root, sp, proj, ep, res, origs[entries[0]])
    return res


def _trace_check(name, root, sp, proj, ep, res, orig):
    """第 7 回 第 2 段: 1 項目ずつ適用して解析したエントリーを壊すか（黙って変わる / 70 で止まる）を見て、
    壊す項目の確認の状態（記録 + run_entry=True / import だけ）を出す。"""
    from mcp_pystub.verifier import verify
    items = [("pkg", p["package_name"]) for p in res["stubbable"] + res.get("conditional", [])]
    items += [("sub", (h["submodule"], h["target_package"])) for h in
              res.get("submodule_stub_hints", []) + res.get("conditional_submodule_hints", [])]
    items += [("nof", p["package_name"]) for p in res["nofollow"]]
    pk = [x for k, x in items if k == "pkg"]
    subs = [f"{x[0]}=>{x[1]}" for k, x in items if k == "sub"]
    nof = [x for k, x in items if k == "nof"]
    vr = verify(ep, python_path=str(sp), packages=pk, submodules=subs, nofollow=nof, run_entry=True, record=True)
    vi = verify(ep, python_path=str(sp), packages=pk, submodules=subs, nofollow=nof)
    for k, x in items:
        one = {"stubbable": [], "nofollow": [], "submodule_stub_hints": [], "conditional": [],
               "conditional_submodule_hints": []}
        if k == "pkg":
            one["stubbable"] = [{"package_name": x}]
            key = x
        elif k == "sub":
            one["submodule_stub_hints"] = [{"submodule": x[0], "target_package": x[1]}]
            key = x[0]
        else:
            one["nofollow"] = [{"package_name": x}]
            key = x
        site, _applied = build_site(root, sp, ep, one, "proven")
        rc, o, er = run_py([str(proj), str(site)], ep)
        if (rc, o) == (orig[0], orig[1]):
            kind = "SAME"
        elif rc == 70 or "PystubConditionalStubError" in (er or ""):
            kind = "STOP70"
        elif rc == 0:
            kind = "SILENT_CHANGE"
        else:
            kind = "CHANGED_ERROR"
        sr = vr["statuses"].get(key, {})
        si = vi["statuses"].get(key, {})
        obs = (sr.get("observed") or {}).get("state")
        print(f"TCHECK {name} {key} {kind} rec={sr.get('status')}/{obs} imp={si.get('status')}", flush=True)


def _verify_check(name, root, sp, proj, ep, res, orig):
    """第 7 回: 適用すると解析したエントリーが変わる場面で、verify(run_entry=True) が通した組を適用しても
    エントリーが変わらないこと（= verify が原因の項目を passed に入れないこと）を確かめる。"""
    from mcp_pystub.verifier import verify
    pk = [p["package_name"] for p in res["stubbable"]] + [p["package_name"] for p in res.get("conditional", [])]
    subs = [f"{h['submodule']}=>{h['target_package']}" for h in
            res.get("submodule_stub_hints", []) + res.get("conditional_submodule_hints", [])]
    nof = [p["package_name"] for p in res["nofollow"]]
    v = verify(ep, python_path=str(sp), packages=pk, submodules=subs, nofollow=nof, run_entry=True)
    passed = {"stubbable": [{"package_name": x} for x in v["passed"]["packages"]], "nofollow":
              [{"package_name": x} for x in v["passed"]["nofollow"]],
              "submodule_stub_hints": [{"submodule": x.split("=>")[0], "target_package": x.split("=>")[1]}
                                       for x in v["passed"]["submodules"] if "=>" in x], "conditional": [],
              "conditional_submodule_hints": []}
    site, applied = build_site(root, sp, ep, passed, "proven")
    rc, o, er = run_py([str(proj), str(site)], ep)
    ok = (rc, o) == (orig[0], orig[1])
    print(f"VCHECK {name} {'ok' if ok else 'BAD'} items={pk + subs + nof} passed={v['passed']} "
          f"rejected={[x['item'] for x in v['rejected']]} baseline_failed={bool(v.get('baseline_failed'))}", flush=True)
