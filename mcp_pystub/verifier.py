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

def run_entry():
    emit({"start": "<entry>"})
    # 普段の実行（python <エントリー> <引数>、エントリーのあるフォルダで）と同じ条件にする
    saved_argv, saved_cwd = sys.argv, os.getcwd()
    sys.argv = [cfg["entry"]] + list(cfg.get("entry_args") or [])
    try:
        os.chdir(cfg.get("cwd") or os.path.dirname(cfg["entry"]))
    except OSError:
        pass
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
        sys.argv = saved_argv
        try:
            os.chdir(saved_cwd)
        except OSError:
            pass
    emit({"m": "<entry>", "ok": rc == 0, "rc": rc, "stdout": buf.getvalue()[-20000:], "err": err})

if cfg.get("entry_first") and cfg["run_entry"]:
    # 実行の記録を取る時は、どの子プロセスもエントリーを先に動かす（普段の実行と同じく、グラフ上のモジュールを
    # 先に import しない）。記録は基準（スタブなし）の実行の時だけ、エントリーの実行の間だけ取る
    if cfg.get("trace_out"):
        _spec = importlib.util.spec_from_file_location("_pystub_trace", cfg["trace_mod"])
        _trm = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_trm)
        _rec = _trm.start(cfg["entry"])
        try:
            run_entry()
        finally:
            _data = _trm.stop(_rec)
            with open(cfg["trace_out"], "w", encoding="utf-8") as _f:
                json.dump(_data, _f, ensure_ascii=False)
    else:
        run_entry()
    cfg["run_entry"] = False

for m in cfg["targets"]:
    emit({"start": m})
    try:
        importlib.import_module(m)
        emit({"m": m, "ok": True})
    except BaseException as e:  # noqa
        emit({"m": m, "ok": False, "err": f"{type(e).__name__}: {e}"[:800], "tb": traceback.format_exc()[-3000:]})
if cfg["run_entry"]:
    run_entry()
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
        cp = subprocess.run([py, "-X", "utf8", str(child), str(cfg_path)], cwd=str(workdir), env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            timeout=timeout)
        _LAST_CHILD["rc"] = cp.returncode
        _LAST_CHILD["stderr"] = (cp.stderr or b"").decode("utf-8", "replace")[-1500:]
    except subprocess.TimeoutExpired:
        timed_out = True
        _LAST_CHILD["rc"] = None
        _LAST_CHILD["stderr"] = "timeout"
    recs: list[dict] = []
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return recs, timed_out


_LAST_CHILD: dict = {}
_BASE_EXTRA: dict = {}  # 基準の実行にだけ渡す設定（実行の記録）  # 直前の子プロセスの終了コードと標準エラー（エントリーが最後まで走らなかった理由に使う）


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
    if not hit and "PystubConditionalStubError" in text:
        # 止まるスタブの説明「mcp-pystub のスタブです。<名前> は…」から項目を特定する
        for key, it in items.items():
            for p in it["prefixes"]:
                if f"のスタブです。{p} は" in text or f"のスタブです。{p} の" in text:
                    hit.append(key)
                    break
        if hit:
            return hit
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
    entry_args: list[str] | None = None,
    cwd: str = "",
    record: bool = False,
    trace_files: list[str] | None = None,
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
    if True:  # 同じグラフ・意味解析を使い回す（モジュールの関数を書き換えない: 同時呼び出しで干渉しない）
        for p in packages:
            g = stub_generator.generate_stubs(str(entry), p, python_path, max_depth, _prepared=prepared)
            if "error" in g:
                errors.append({"item": p, "reason": "generate error", "error": g["error"]})
                continue
            items[p] = {"kind": "package", "files": g["files"], "prefixes": [p], "block": []}
        for s in submodules:
            sub, _, target = s.partition("=>")
            parent = sub.split(".")[0]
            g = stub_generator.generate_submodule_stubs(str(entry), parent, sub, python_path, max_depth,
                                                        _prepared=prepared)
            if "error" in g:
                errors.append({"item": sub, "reason": "generate error", "error": g["error"]})
                continue
            if sub in items:
                # 同じサブモジュールを対象違いで渡された: 外す対象を足す（上書きしない）
                if target and target not in items[sub]["block"]:
                    items[sub]["block"].append(target)
                    items[sub]["targets"].append(target)
                continue
            items[sub] = {"kind": "submodule", "files": g["files"], "prefixes": [sub],
                          "block": [target] if target else [], "targets": [target] if target else []}
        for n in nofollow:
            items[f"nofollow:{n}"] = {"kind": "nofollow", "files": {}, "prefixes": [], "block": [n]}


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
        return {**_BASE_EXTRA, "entry_first": bool(record and run_entry), "stubs": stubs, "prefixes": prefixes,
                "block": block, "targets": tgts, "sys_path": sys_path,
                "entry": str(entry), "run_entry": run_entry, "entry_args": list(entry_args or []),
                "cwd": str(Path(cwd).resolve()) if cwd else str(Path(entry).parent)}

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

    trace_path = work / "base_trace.json"
    base_cfg_extra = {}
    if record and run_entry:
        base_cfg_extra = {"trace_out": str(trace_path), "trace_mod": str(Path(__file__).with_name("trace.py"))}
    _BASE_EXTRA.clear()
    _BASE_EXTRA.update(base_cfg_extra)
    base, base_hung = run_all([], targets)
    _BASE_EXTRA.clear()
    base_child = dict(_LAST_CHILD)  # 基準の子プロセス全体の終了コード・標準エラー（エントリーの後の atexit も含む）
    ok_targets = [m for m in targets if base.get(m, {}).get("ok")]
    base_entry = base.get("<entry>")
    entry_note = ""
    if run_entry and base_entry is None:
        # import の途中で固まったなどで、基準のエントリー実行が済んでいない: 単独でやり直す
        recs, t_out = _run_child(py, cfg_for([], []), work, timeout)
        r0, _l, _d = _results(recs)
        base_entry = r0.get("<entry>")
        base_child = dict(_LAST_CHILD)
        if base_entry is None:
            entry_note = ("基準（スタブなし）のエントリー実行が" + ("時間内に終わらない" if t_out else "できない")
                          + "ため、エントリーの出力の一致は確かめていません")

    # 基準（スタブなし）が失敗したら、何も通さない（比べる元が無い: 第 7 回）
    baseline_failed = None
    why_b = (base_child.get("stderr") or "").strip()
    if targets and not ok_targets:
        baseline_failed = {"reason": "基準（スタブなし）で import できたモジュールが 0 件です",
                           "rc": base_child.get("rc"), "stderr_tail": why_b[-800:]}
    elif run_entry and base_entry is None:
        baseline_failed = {"reason": "基準（スタブなし）でエントリーが最後まで実行されませんでした",
                           "rc": base_child.get("rc"), "stderr_tail": why_b[-800:]}
    elif run_entry and (base_entry.get("rc") not in (0, None) or base_child.get("rc") not in (0, None)):
        baseline_failed = {"reason": f"基準（スタブなし）のエントリーが終了コード "
                                     f"{base_entry.get('rc')}（子プロセス全体 {base_child.get('rc')}）で終わりました",
                           "rc": base_child.get("rc"), "stderr_tail": ((base_entry.get("err") or "") + why_b)[-800:]}
    if baseline_failed is not None:
        baseline_failed["hint"] = ("エントリーが引数・入力ファイル・作業フォルダを必要とするなら entry_args / cwd を指定して"
                                   "ください。メモリ不足などで基準の実行が途中で終わった場合は、空きを確保して回し直してください")

    active = [k for k in items] if baseline_failed is None else []
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

    def failures(keys: list[str]) -> tuple[list[dict], dict, list[str]]:
        tg = visible(keys)
        res, _h = run_all(keys, tg)
        fl = [r for m, r in res.items() if m != "<entry>" and not r.get("ok")]
        miss = [m for m in tg if m not in res]
        if miss and not fl:
            # 子プロセスが import の途中で終わった（止まるスタブの os._exit(70) など）: 最初の未完了のモジュールを失敗として扱う
            why = (_LAST_CHILD.get("stderr") or "").strip()
            fl.append({"m": miss[0], "ok": False, "err": (
                f"{miss[0]} の import の途中で子プロセスが終了した（終了コード {_LAST_CHILD.get('rc')}）: "
                + (why.splitlines()[-1] if why else "標準エラーなし"))})
        if run_entry and base_entry is not None:
            er = res.get("<entry>")
            if er is not None and _LAST_CHILD.get("rc") != base_child.get("rc") \
                    and er.get("rc") == base_entry.get("rc") and er.get("stdout") == base_entry.get("stdout"):
                why = (_LAST_CHILD.get("stderr") or "").strip()
                fl.append({"m": "<entry>", "entry_mismatch": True, "err": (
                    f"エントリーの後（atexit 等）で子プロセスの終了コードが変わった: 基準 {base_child.get('rc')} → "
                    f"{_LAST_CHILD.get('rc')}: " + (why.splitlines()[-1] if why else "標準エラーなし"))})
            elif er is None or er.get("rc") != base_entry.get("rc") or er.get("stdout") != base_entry.get("stdout"):
                if er is None:
                    why = _LAST_CHILD.get("stderr", "") or ""
                    er = {"m": "<entry>", "err": (
                        f"エントリーが最後まで実行されなかった（終了コード {_LAST_CHILD.get('rc')}）: "
                        + (why.strip().splitlines()[-1] if why.strip() else "標準エラーなし"))}
                fl.append(dict(er, m="<entry>", entry_mismatch=True))
        return fl, res, miss

    unresolved: list[dict] = []
    while active and rounds < max_rounds:
        rounds += 1
        fails, res, missing = failures(active)
        last_res = res
        if not fails and not missing:
            break
        blamed: dict[str, dict] = {}
        for r in fails:
            for k in _blame(r, {k: items[k] for k in active}):
                blamed.setdefault(k, r)
        if not blamed:
            # 原因を特定できない（例外を伴わない出力の不一致など）: 1 つずつ外して、外すと失敗が消える項目を探す
            sig = {(r.get("m"), bool(r.get("entry_mismatch"))) for r in fails}
            for k in list(active):
                trial = [x for x in active if x != k]
                f2, _r2, _m2 = failures(trial)
                if not ({(r.get("m"), bool(r.get("entry_mismatch"))) for r in f2} & sig):
                    blamed[k] = fails[0]
                    break
            if not blamed:
                for k in active:
                    unresolved.append({"item": k, "reason": "原因を特定できない失敗があるため、通ったとは扱いません（特定できず）",
                                       "module": fails[0].get("m") if fails else "",
                                       "error": (fails[0].get("err") if fails else "") or
                                       ("エントリーの出力が元と違います" if fails and fails[0].get("entry_mismatch") else "")})
                active = []
                break
        for k, r in blamed.items():
            silent = bool(r.get("entry_mismatch")) and not (r.get("err") or "").strip()
            rejected.append({"item": k, "reason": ("スタブに差し替えるとエントリーの出力が変わった（例外は出ていない）" if silent
                                                   else "スタブに差し替えると import / 実行が失敗"),
                             "module": r.get("m"), "error": r.get("err"),
                             "traceback_tail": (r.get("tb") or "")[-1200:]})
        active = [k for k in active if k not in blamed]
    else:
        if active and rounds >= max_rounds:
            # 回数の上限に達した: 残りを通ったとは扱わない
            fails, res, missing = failures(active)
            last_res = res
            if fails or missing:
                for k in active:
                    unresolved.append({"item": k, "reason": f"{max_rounds} 回で原因を特定しきれませんでした（特定できず）",
                                       "module": fails[0].get("m") if fails else "", "error": fails[0].get("err") if fails else ""})
                active = []

    # 実行の記録（指定された時だけ）: 基準の実行の記録と、渡された記録ファイルを合算し、各項目に観測の区分を付ける
    from mcp_pystub import trace as _trace
    trace_data = None
    if record and run_entry and trace_path.exists():
        try:
            trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            trace_data = None
    if trace_files:
        trace_data = _trace.merge(trace_data, _trace.load(trace_files)) if trace_data else _trace.load(trace_files)
    site_dirs = [str(Path(python_path).resolve())] if python_path else [str(p) for p in resolver._site_packages]
    statuses: dict = {}
    for k, it in items.items():
        name = k.split(":", 1)[1] if it["kind"] == "nofollow" else k
        obs = _trace.observe(trace_data, name, site_dirs) if trace_data else None
        if it["kind"] == "submodule" and obs is not None:
            obs["targets"] = {t: _trace.observe(trace_data, t, site_dirs)["state"] for t in it.get("targets", [])}
        ok = k in active
        if ok and run_entry and obs is not None and obs["state"] in ("not_imported", "import_only"):
            st = "confirmed_run"  # 記録で未実行、かつエントリーの差し替え実行を通った
        elif ok:
            st = "confirmed_import"  # import（と、指定があればエントリーの実行）を通った。実行の記録での確認は無い
        else:
            st = "unconfirmed"
        statuses[name] = {"status": st, "observed": obs, "entry_run_passed": bool(ok and run_entry)}
    for r in rejected + unresolved:
        statuses.setdefault(r["item"], {"status": "unconfirmed", "observed": None, "entry_run_passed": False})
    labels = {"confirmed_run": "確認済み（実行）", "confirmed_import": "import 確認済み", "unconfirmed": "未確認"}
    status_summary = {labels[k]: [n for n, v in statuses.items() if v["status"] == k] for k in labels}

    passed = {
        "packages": [k for k in active if items[k]["kind"] == "package"],
        "submodules": [f"{k}=>{t}" if t else k for k in active if items[k]["kind"] == "submodule"
                       for t in (items[k]["targets"] or [""])],
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
        "status_summary": status_summary,
        "statuses": statuses,
        "traced": trace_data is not None,
        "status_note": ("確認済み（実行）= 実行の記録で未実行（not_imported / import_only）かつ run_entry=True の差し替え実行を通った。"
                        "import 確認済み = スタブに差し替えた import（と、run_entry=True ならエントリーの実行）を通ったが、"
                        "実行の記録での確認は無い / 記録では実行されていた。未確認 = 通らなかった・確かめられなかった。"
                        "記録は動かした入力の範囲だけで、子プロセスの中と拡張モジュール（.pyd / .so）の中は記録できません。"),
        "baseline_failed": baseline_failed,
        "rejected": rejected,
        "unresolved": unresolved,
        "imported_modules": len(ok_targets),
        "baseline_failures": len(targets) - len(ok_targets),
        "baseline_hung": base_hung,
        "run_entry": run_entry,
        "entry_matched": (None if not run_entry or base_entry is None else (
            bool(active) and last_res.get("<entry>", {}).get("stdout") == (base_entry or {}).get("stdout")
        )),
        "entry_note": entry_note if run_entry else
        "run_entry=False のため、エントリーの実行（出力の一致）は確かめていません。import だけを確かめました。",
        "rounds": rounds,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "python_note": py_note,
        "note": "verify は import（と run_entry=True ならエントリーの 1 回の実行）で確かめるだけで、"
                "全ての実行経路の保証ではありません。ビルド後の実行確認も行ってください。"
                + ("" if run_entry else "run_entry=False（既定）は import だけの確認で、各モジュールの直下のコードは動きますが、"
                   "エントリーの if __name__ == \"__main__\": の中は動きません。")
                + "比べるのはエントリーの標準出力（実行中に sys.stdout へ書いたもの）と終了コード（エントリーと子プロセス全体）"
                "だけで、標準エラー・ファイルへの出力・import 時に掴まれた stdout への出力は比べません。"
                "エントリーは sys.argv = [エントリー] + entry_args、作業フォルダ cwd（既定はエントリーのあるフォルダ）で実行します。",
    }
    shutil.rmtree(work, ignore_errors=True)
    return out
