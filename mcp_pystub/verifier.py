"""動的検証（verify ツール）

静的解析（analyze）は候補を出すだけで、実行の保証ではない。verify は生成したスタブを一時フォルダに置き、
**対象環境の python を子プロセスで起動**して、import グラフ上のモジュールを実際に import する。

- 対象環境の site-packages は書き換えない（スタブは meta path finder で差し込む。サブモジュールの
  ヒントでは対象の C 拡張パッケージを import 不可にして、本当に要らないことを確かめる）
- まずスタブ無しで同じ import を行い（基準）、スタブありで新たに失敗したものを「スタブが原因」とする
- 失敗の原因になったスタブを外して繰り返し、検証を通った組を返す
- エントリー本体の実行は run_entry=True のときだけ（出力・終了コードを基準と比べる）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp_pystub import stub_generator
from mcp_pystub.semantics import under
from mcp_pystub.usage_analyzer import analyze_packages, get_semantics

_CHILD = r'''
import importlib, importlib.abc, importlib.machinery, importlib.util, io, json, os, runpy, sys, traceback
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
STUBS = cfg["stubs"]            # module name -> stub file
PREFIXES = cfg["prefixes"]      # stubbed prefixes (names under them come only from STUBS)
BLOCK = cfg["block"]            # prefixes that must not be importable (nofollow / hint targets)
out = open(cfg["out"], "a", encoding="utf-8")

def _under(n, p):
    return n == p or n.startswith(p + ".")

class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        for b in BLOCK:
            if _under(name, b):
                raise ModuleNotFoundError(f"No module named {name!r} (excluded by verify)", name=name)
        f = STUBS.get(name)
        if f:
            if os.path.basename(f) == "__init__.py":
                return importlib.util.spec_from_file_location(name, f, submodule_search_locations=[os.path.dirname(f)])
            return importlib.util.spec_from_file_location(name, f)
        for p in PREFIXES:
            if _under(name, p):
                raise ModuleNotFoundError(f"No module named {name!r} (not in stub)", name=name)
        return None

if STUBS or BLOCK:
    sys.meta_path.insert(0, _Finder())
for d in reversed(cfg["sys_path"]):
    sys.path.insert(0, d)

def emit(rec):
    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    out.flush()

for m in cfg["targets"]:
    emit({"start": m})
    try:
        importlib.import_module(m)
        emit({"m": m, "ok": True})
    except BaseException as e:  # noqa
        emit({"m": m, "ok": False, "err": f"{type(e).__name__}: {e}"[:800], "tb": traceback.format_exc()[-3000:]})
if cfg["run_entry"]:
    emit({"start": "<entry>"})
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    rc = 0
    err = ""
    try:
        runpy.run_path(cfg["entry"], run_name="__main__")
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException as e:  # noqa
        rc = 1
        err = f"{type(e).__name__}: {e}"[:800] + "\n" + traceback.format_exc()[-3000:]
    finally:
        sys.stdout = old
    emit({"m": "<entry>", "ok": rc == 0, "rc": rc, "stdout": buf.getvalue()[-20000:], "err": err})
emit({"done": True})
'''


def _target_python(python_path: str) -> str | None:
    """python_path（site-packages）に対応する python の実行ファイル。"""
    if not python_path:
        return sys.executable
    sp = Path(python_path).resolve()
    cands = []
    for up in (sp.parent.parent, sp.parent.parent.parent, sp.parent):
        cands += [up / "Scripts" / "python.exe", up / "python.exe", up / "bin" / "python3", up / "bin" / "python"]
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def _is_test_module(name: str) -> bool:
    parts = name.split(".")
    return any(p in ("tests", "test", "testing", "conftest", "__main__", "_pytest_plugin") for p in parts[1:]) or \
        name.endswith(("_test", ".setup"))


def _run_child(py: str, cfg: dict, workdir: Path, timeout: float) -> tuple[list[dict], bool]:
    cfg_path = workdir / f"cfg_{time.time_ns()}.json"
    out_path = workdir / f"out_{time.time_ns()}.jsonl"
    cfg = dict(cfg, out=str(out_path))
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    child = workdir / "child.py"
    if not child.exists():
        child.write_text(_CHILD, encoding="utf-8")
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg",
                "QT_QPA_PLATFORM": "offscreen", "PYTHONNOUSERSITE": "1" if cfg.get("isolate_user") else ""})
    env.pop("PYTHONPATH", None)
    timed_out = False
    try:
        subprocess.run([py, "-X", "utf8", str(child), str(cfg_path)], cwd=str(workdir), env=env,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    recs: list[dict] = []
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return recs, timed_out


def _results(recs: list[dict]) -> tuple[dict[str, dict], str | None, bool]:
    res: dict[str, dict] = {}
    last_start = None
    done = False
    for r in recs:
        if "start" in r:
            last_start = r["start"]
        elif "m" in r:
            res[r["m"]] = r
            last_start = None
        elif r.get("done"):
            done = True
    return res, last_start, done


def _blame(rec: dict, items: dict[str, dict]) -> list[str]:
    """失敗の記録から、原因のスタブ（項目のキー）を推定する。"""
    text = (rec.get("err") or "") + "\n" + (rec.get("tb") or "")
    hit: list[str] = []
    for key, it in items.items():
        d = it["dir"].replace("\\", "/")
        if d in text.replace("\\", "/"):
            hit.append(key)
            continue
        for p in it["prefixes"] + it["block"]:
            if f"'{p}'" in text or f"'{p}." in text or f"{p} (stub)" in text:
                hit.append(key)
                break
    if not hit and "(stub)" in text:
        # RuntimeError: <名前> (stub) — 名前からは項目が分からない
        return []
    return hit


def verify(
    entry_point: str,
    python_path: str = "",
    packages: list[str] | None = None,
    submodules: list[str] | None = None,
    nofollow: list[str] | None = None,
    run_entry: bool = False,
    max_depth: int = 10,
    timeout: float = 900.0,
    max_rounds: int = 8,
) -> dict:
    """スタブ（とサブモジュールのヒント・nofollow）に差し替えた状態で、実際に import して確かめる。

    packages / submodules / nofollow を省略すると analyze の提案（stubbable / submodule_stub_hints / nofollow）を使う。
    """
    t0 = time.monotonic()
    prepared, err = stub_generator._prepare(entry_point, python_path, max_depth)
    if err:
        return err
    entry, resolver, graph = prepared
    py = _target_python(python_path)
    py_note = ""
    if py is None:
        # venv ではない site-packages: 現在の python に python_path を足して確かめる
        py = sys.executable
        py_note = f"python_path に対応する python が見つからないため {py} に python_path を足して確かめました"

    sem = get_semantics(graph)
    if packages is None or submodules is None or nofollow is None:
        analyses = analyze_packages(graph)
        if packages is None:
            packages = [a.package_name for a in analyses if a.verdict == "stubbable"]
        if nofollow is None:
            nofollow = [a.package_name for a in analyses if a.verdict == "nofollow"]
        if submodules is None:
            submodules = []
            hints = []
            for a in analyses:
                for h in a.submodule_stubs:
                    hints.append(h)
            submodules = [f"{h.submodule}=>{h.target_package}" for h in hints]

    work = Path(tempfile.mkdtemp(prefix="pystub_verify_"))
    items: dict[str, dict] = {}
    errors: list[dict] = []
    real_gen = stub_generator._prepare
    stub_generator._prepare = lambda *a, **k: (prepared, None)  # 同じグラフ・意味解析を使い回す
    try:
        for p in packages:
            g = stub_generator.generate_stubs(str(entry), p, python_path, max_depth)
            if "error" in g:
                errors.append({"item": p, "reason": "generate error", "error": g["error"]})
                continue
            items[p] = {"kind": "package", "files": g["files"], "prefixes": [p], "block": []}
        for s in submodules:
            sub, _, target = s.partition("=>")
            parent = sub.split(".")[0]
            g = stub_generator.generate_submodule_stubs(str(entry), parent, sub, python_path, max_depth)
            if "error" in g:
                errors.append({"item": sub, "reason": "generate error", "error": g["error"]})
                continue
            items[sub] = {"kind": "submodule", "files": g["files"], "prefixes": [sub],
                          "block": [target] if target else []}
        for n in nofollow:
            items[f"nofollow:{n}"] = {"kind": "nofollow", "files": {}, "prefixes": [], "block": [n]}
    finally:
        stub_generator._prepare = real_gen

    # スタブのファイルを項目ごとのフォルダに書き出す
    stub_map: dict[str, dict[str, str]] = {}
    for key, it in items.items():
        d = work / ("stub_" + key.replace(":", "_").replace(".", "_"))
        it["dir"] = str(d)
        mm: dict[str, str] = {}
        for rel, code in it["files"].items():
            f = d / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(code, encoding="utf-8")
            mod = rel[:-3].replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            mm[mod] = str(f)
        stub_map[key] = mm

    # import する対象: グラフ上のモジュール（エントリー本体とテスト用モジュールは除く）
    entry_mod = sem.entry_module
    order = sorted(graph.nodes.items(), key=lambda kv: (kv[1].depth, kv[0]))
    targets = [
        name for name, node in order
        if name != entry_mod and node.classification in ("local", "third_party") and node.file_path
        and not _is_test_module(name)
    ]
    sys_path = [str(Path(entry).parent)]
    if python_path:
        sys_path.append(str(Path(python_path).resolve()))

    def cfg_for(keys: list[str], tgts: list[str]) -> dict:
        stubs: dict[str, str] = {}
        prefixes: list[str] = []
        block: list[str] = []
        for k in keys:
            stubs.update(stub_map[k])
            prefixes += items[k]["prefixes"]
            block += items[k]["block"]
        return {"stubs": stubs, "prefixes": prefixes, "block": block, "targets": tgts, "sys_path": sys_path,
                "entry": str(entry), "run_entry": run_entry}

    def run_all(keys: list[str], tgts: list[str]) -> tuple[dict[str, dict], list[str]]:
        """固まるモジュールは飛ばして最後まで import する。"""
        remaining = list(tgts)
        res: dict[str, dict] = {}
        hung: list[str] = []
        for _ in range(6):
            recs, timed_out = _run_child(py, cfg_for(keys, remaining), work, timeout)
            r, last, done = _results(recs)
            res.update(r)
            if done or not timed_out:
                break
            if last is None:
                break
            hung.append(last)
            res[last] = {"m": last, "ok": False, "err": "timeout", "tb": ""}
            idx = remaining.index(last) + 1 if last in remaining else len(remaining)
            remaining = remaining[idx:]
            if not remaining:
                break
        return res, hung

    base, base_hung = run_all([], targets)
    ok_targets = [m for m in targets if base.get(m, {}).get("ok")]
    base_entry = base.get("<entry>")

    active = [k for k in items]
    rejected: list[dict] = list(errors)
    rounds = 0
    last_res: dict[str, dict] = {}
    def visible(keys: list[str]) -> list[str]:
        """差し替えた範囲の内側でスタブに無いモジュール・外したモジュールは、直接 import の対象にしない。

        それらは置き換え（除外）によって無くなるのが前提なので、直接 import すれば必ず失敗する（誤った却下）。
        本当に必要なら、それを import する外側のモジュールやエントリーの実行が失敗して分かる。
        """
        hidden_pref: list[str] = []
        provided: set[str] = set()
        for k in keys:
            hidden_pref += items[k]["prefixes"] + items[k]["block"]
            provided |= set(stub_map[k])
        return [m for m in ok_targets if m in provided or not any(under(m, x) for x in hidden_pref)]

    while active and rounds < max_rounds:
        rounds += 1
        tg = visible(active)
        res, _h = run_all(active, tg)
        last_res = res
        fails = [r for m, r in res.items() if m != "<entry>" and not r.get("ok")]
        missing = [m for m in tg if m not in res]
        if run_entry and base_entry is not None:
            er = res.get("<entry>")
            if er is None or er.get("rc") != base_entry.get("rc") or er.get("stdout") != base_entry.get("stdout"):
                fails.append(dict(er or {"m": "<entry>", "err": "entry not run"}, entry_mismatch=True))
        if not fails and not missing:
            break
        blamed: dict[str, dict] = {}
        for r in fails:
            for k in _blame(r, {k: items[k] for k in active}):
                blamed.setdefault(k, r)
        if not blamed:
            # 原因を特定できない: 1 つずつ外して確かめる
            for k in list(active):
                trial = [x for x in active if x != k]
                res2, _ = run_all(trial, visible(trial))
                f2 = [r for m, r in res2.items() if m != "<entry>" and not r.get("ok")]
                if len(f2) < len(fails):
                    blamed[k] = fails[0]
                    break
            if not blamed:
                for k in active:
                    rejected.append({"item": k, "reason": "原因を特定できない失敗があるため全て外しました",
                                     "module": fails[0].get("m") if fails else "", "error": (fails[0].get("err") if fails else "")})
                active = []
                break
        for k, r in blamed.items():
            rejected.append({"item": k, "reason": "スタブに差し替えると import / 実行が失敗",
                             "module": r.get("m"), "error": r.get("err"),
                             "traceback_tail": (r.get("tb") or "")[-1200:]})
        active = [k for k in active if k not in blamed]

    passed = {
        "packages": [k for k in active if items[k]["kind"] == "package"],
        "submodules": [k for k in active if items[k]["kind"] == "submodule"],
        "nofollow": [k.split(":", 1)[1] for k in active if items[k]["kind"] == "nofollow"],
    }
    out = {
        "python": py,
        "checked": {
            "packages": [k for k in items if items[k]["kind"] == "package"],
            "submodules": [k for k in items if items[k]["kind"] == "submodule"],
            "nofollow": [k.split(":", 1)[1] for k in items if items[k]["kind"] == "nofollow"],
        },
        "passed": passed,
        "rejected": rejected,
        "imported_modules": len(ok_targets),
        "baseline_failures": len(targets) - len(ok_targets),
        "baseline_hung": base_hung,
        "run_entry": run_entry,
        "entry_matched": (None if not run_entry else (
            bool(active) and last_res.get("<entry>", {}).get("stdout") == (base_entry or {}).get("stdout")
        )),
        "rounds": rounds,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "python_note": py_note,
        "note": "verify は import（と run_entry=True ならエントリーの 1 回の実行）で確かめるだけで、"
                "全ての実行経路の保証ではありません。ビルド後の実行確認も行ってください。",
    }
    shutil.rmtree(work, ignore_errors=True)
    return out
