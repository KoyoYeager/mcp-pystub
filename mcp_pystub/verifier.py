"""動的検証（verify ツール）

静的解析（analyze）は候補を出すだけで、実行の保証ではない。verify は生成したスタブを一時フォルダに置き、
**対象環境の python を子プロセスで起動**して、import グラフ上のモジュールを実際に import する。

- 対象環境の site-packages は書き換えない（スタブは meta path finder で差し込む。サブモジュールの
  ヒントでは対象の C 拡張パッケージを import 不可にして、本当に要らないことを確かめる）
- まずスタブ無しで同じ import を行い（基準）、スタブありで新たに失敗したものを「スタブが原因」とする
- 失敗の原因になったスタブを外して繰り返し、検証を通った組を返す
- エントリーの実行は run_entry=True のときだけ。普段の起動（python entry.py 引数…、標準出力はファイル）と同じ条件の
  子プロセスで動かし、標準出力のバイト列の全体と終了コードを基準と比べる（主な違いは PYTHONPATH の先頭の起動用フォルダと
  環境変数 MCP_PYSTUB_BOOT の 2 つ。ほかは README の表）
- import の確認は別の子プロセスで行い、エントリーのスクリプトは動かさない（エントリーを import するローカルの
  モジュールは対象から外し、import の途中でエントリーのファイルを open で開く操作は、ファイルの同一性で見分けて監査フックで止める。
  OS のコピー API・ctypes・外部コマンドでの読み込みは止められない）
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
import importlib, json, os, sys, traceback
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
out = open(cfg["out"], "a", encoding="utf-8")
# import だけの確認。スタブの差し込みと監査フック（エントリーのファイルを開く操作を止める）は、起動時に
# sitecustomize（PYTHONPATH の先頭の起動用フォルダ）が始めている
BOOT = getattr(sys, "_pystub_boot", None)
for d in reversed(cfg["sys_path"]):
    sys.path.insert(0, d)

def emit(rec):
    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    out.flush()

emit({"startup_loaded": (BOOT or {}).get("startup_loaded", []), "boot": BOOT is not None})
for m in cfg["targets"]:
    emit({"start": m})
    if BOOT is not None:
        BOOT["current"] = m
    nb = len(BOOT["state"]["blocked"]) if BOOT is not None else 0
    try:
        importlib.import_module(m)
        emit({"m": m, "ok": True})
    except BaseException as e:  # noqa
        blocked = BOOT is not None and len(BOOT["state"]["blocked"]) > nb
        emit({"m": m, "ok": False, "err": f"{type(e).__name__}: {e}"[:800], "tb": traceback.format_exc()[-3000:],
              "blocked_entry": blocked})
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


def _run_import_child(py: str, cfg: dict, workdir: Path, timeout: float, last: dict) -> tuple[list[dict], bool]:
    """import だけの確認の子プロセス（エントリーは動かさない）。"""
    from mcp_pystub import proc as _proc
    ns = time.time_ns()
    cfg_path = workdir / f"cfg_{ns}.json"
    out_path = workdir / f"out_{ns}.jsonl"
    err_path = workdir / f"err_{ns}.txt"
    cfg = dict(cfg, out=str(out_path))
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    child = workdir / "child.py"
    if not child.exists():
        child.write_text(_CHILD, encoding="utf-8")
    boot_dir = workdir / "boot"
    _proc.write_boot(boot_dir)
    proc_dir = workdir / f"procs_{ns}"
    boot_cfg = {"stubs": cfg["stubs"], "prefixes": cfg["prefixes"], "block": cfg["block"], "watch": cfg.get("watch", []),
                "entry": cfg["entry"], "entry_guard": True, "extra_path": cfg.get("extra_path", []),
                "proc_dir": str(proc_dir)}
    boot_path = workdir / f"boot_{ns}.json"
    boot_path.write_text(json.dumps(boot_cfg, ensure_ascii=False), encoding="utf-8")
    env = _proc.boot_env(boot_dir, boot_path)
    with open(err_path, "wb") as fe:
        rc, timed_out = _proc.run_tree([py, str(child), str(cfg_path)], str(workdir), env, timeout,
                                       stdout=subprocess.DEVNULL, stderr=fe)
    last["rc"] = rc
    last["stderr"] = "timeout" if timed_out else _decode(err_path.read_bytes()[-3000:])
    recs: list[dict] = []
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    procs = _proc.read_procs(proc_dir)
    last["startup_loaded"] = next((r["startup_loaded"] for r in recs if "startup_loaded" in r), [])
    for pr in procs:
        last["startup_loaded"] = sorted(set(last["startup_loaded"]) | set(pr.get("startup_loaded") or []))
    last["blocked"] = sorted({b for pr in procs for b in (pr.get("blocked") or []) if b})
    _proc.rmtree_retry(proc_dir)
    return recs, timed_out


def _decode(b: bytes) -> str:
    """表示・原因の特定用のデコード（比較には使わない）。UTF-8 で読めなければ、子プロセスの既定（ロケール）の符号化で読む。"""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        return b.decode(locale.getpreferredencoding(False) or "utf-8", "replace")


def _entry_command(py: str, entry: Path, entry_args: list[str]) -> tuple[list[str], str]:
    """普段の起動のコマンドと既定の作業フォルダ（python entry.py 引数… / パッケージの中なら python -m pkg.mod 引数…）。"""
    from mcp_pystub.import_graph import entry_module_name
    try:
        name, root = entry_module_name(entry)
    except Exception:  # noqa: BLE001
        name, root = entry.stem, entry.parent
    if Path(root).resolve() != entry.parent.resolve() and "." in name:
        return [py, "-m", name] + list(entry_args), str(root)
    return [py, str(entry)] + list(entry_args), str(entry.parent)


def _run_entry_proc(py: str, cmd: list[str], cwd: str, boot_cfg: dict, workdir: Path, timeout: float,
                    recording: bool) -> dict:
    """エントリーを普段の起動と同じ条件で動かす（標準出力はファイルにつなぎ、バイト列の全体を比べる）。"""
    import hashlib
    from mcp_pystub import proc as _proc
    ns = time.time_ns()
    boot_dir = workdir / "boot"
    _proc.write_boot(boot_dir)
    proc_dir = workdir / f"eprocs_{ns}"
    boot_path = workdir / f"eboot_{ns}.json"
    boot_path.write_text(json.dumps(dict(boot_cfg, proc_dir=str(proc_dir)), ensure_ascii=False), encoding="utf-8")
    env = _proc.boot_env(boot_dir, boot_path)
    out_path, err_path = workdir / f"eout_{ns}.bin", workdir / f"eerr_{ns}.bin"
    with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
        rc, timed_out = _proc.run_tree(cmd, cwd, env, timeout, stdout=fo, stderr=fe)
    data = out_path.read_bytes()
    err = _decode(err_path.read_bytes()[-4000:])
    shown = _decode(data)
    if len(shown) > 4000:
        shown = shown[:2000] + "\n…\n" + shown[-2000:]
    procs = _proc.read_procs(proc_dir)
    _proc.rmtree_retry(proc_dir)
    for f in (out_path, err_path, boot_path):
        try:
            f.unlink()
        except OSError:
            pass
    lines = [x for x in err.strip().splitlines() if x.strip()]
    return {"m": "<entry>", "rc": rc, "timed_out": timed_out, "ok": rc == 0 and not timed_out,
            "stdout_key": f"{len(data)}:{hashlib.sha256(data).hexdigest()}", "stdout": shown,
            "err": ("timeout" if timed_out else (lines[-1] if lines else "")), "tb": err,
            "procs": procs, "summary": _proc.summarize_procs(procs, recording)}


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


def verify(*args, **kwargs) -> dict:
    """スタブ（とサブモジュールのヒント・nofollow）に差し替えた状態で、実際に import して確かめる（_verify の後始末つき）。"""
    holder: list[Path] = []
    from mcp_pystub.proc import cleanup_stale_temp
    cleanup_stale_temp()  # 呼び出し元が強制終了されて残った古い一時フォルダ（1 日以上前）を消す
    try:
        return _verify(holder, *args, **kwargs)
    finally:
        from mcp_pystub.proc import rmtree_retry
        for w in holder:
            rmtree_retry(w)


def _verify(
    _holder: list,
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
    _holder.append(work)
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
    entry_norm = os.path.normcase(str(Path(entry).resolve()))
    importers: dict[str, set[str]] = {}
    for e in graph.edges:
        importers.setdefault(e.to_module, set()).add(e.from_module)
    seeds = {n for n, node in graph.nodes.items()
             if n == entry_mod or (node.file_path and os.path.normcase(str(Path(node.file_path).resolve())) == entry_norm)}
    excluded_entry: set[str] = set(seeds)
    todo = list(seeds)
    while todo:
        n = todo.pop()
        for f in importers.get(n, ()):
            node = graph.nodes.get(f)
            if f not in excluded_entry and node is not None and node.classification == "local":
                excluded_entry.add(f)
                todo.append(f)
    targets = [
        name for name, node in order
        if name not in excluded_entry and node.classification in ("local", "third_party") and node.file_path
        and not _is_test_module(name)
    ]
    excluded_entry_shown = sorted(x for x in excluded_entry if x != entry_mod and x in graph.nodes)
    sys_path = [str(Path(entry).parent)]
    if python_path:
        sys_path.append(str(Path(python_path).resolve()))

    watch = sorted({p for it in items.values() for p in it["prefixes"] + it["block"]})
    extra_path = [str(Path(python_path).resolve())] if python_path else []

    def stub_cfg(keys: list[str]) -> dict:
        stubs: dict[str, str] = {}
        prefixes: list[str] = []
        block: list[str] = []
        for k in keys:
            stubs.update(stub_map[k])
            prefixes += items[k]["prefixes"]
            block += items[k]["block"]
        return {"stubs": stubs, "prefixes": prefixes, "block": block}

    def cfg_for(keys: list[str], tgts: list[str]) -> dict:
        return {**stub_cfg(keys), "watch": watch, "extra_path": extra_path, "targets": tgts, "sys_path": sys_path,
                "entry": str(entry)}

    last: dict = {}  # 直前の import の子プロセスの終了コード・標準エラー（呼び出しごとの状態）

    def run_all(keys: list[str], tgts: list[str]) -> tuple[dict[str, dict], list[str]]:
        """固まるモジュールは飛ばして最後まで import する。"""
        remaining = list(tgts)
        res: dict[str, dict] = {}
        hung: list[str] = []
        for _ in range(6):
            recs, timed_out = _run_import_child(py, cfg_for(keys, remaining), work, timeout, last)
            r, lst, done = _results(recs)
            res.update(r)
            if done or not timed_out:
                break
            if lst is None:
                break
            hung.append(lst)
            res[lst] = {"m": lst, "ok": False, "err": "timeout", "tb": ""}
            idx = remaining.index(lst) + 1 if lst in remaining else len(remaining)
            remaining = remaining[idx:]
            if not remaining:
                break
        return res, hung

    ecmd, ecwd = _entry_command(py, Path(entry), list(entry_args or []))
    if cwd:
        ecwd = str(Path(cwd).resolve())
    trace_mod = str(Path(__file__).with_name("trace.py"))

    def run_entry_with(keys: list[str], recording: bool = False) -> dict:
        bc = dict(stub_cfg(keys), watch=watch, entry=str(entry), extra_path=extra_path)
        if recording:
            bc["trace_mod"] = trace_mod
        return _run_entry_proc(py, ecmd, ecwd, bc, work, timeout, recording)

    base, base_hung = run_all([], targets)
    base_imp = dict(last)
    base_startup = set(base_imp.get("startup_loaded") or [])
    blocked_entry = sorted(set(base_imp.get("blocked") or []) |
                           {m for m, r in base.items() if r.get("blocked_entry")})
    ok_targets = [m for m in targets if base.get(m, {}).get("ok")]
    base_entry = None
    entry_note = ""
    if run_entry:
        base_entry = run_entry_with([], recording=bool(record))
        for pr in base_entry["procs"]:
            base_startup |= set(pr.get("startup_loaded") or [])
        if base_entry["timed_out"]:
            entry_note = f"基準（スタブなし）のエントリー実行が {timeout} 秒で終わらなかったため、出力の一致は確かめていません"
    base_procs = list(base_entry["procs"]) if base_entry else []
    base_sum = base_entry["summary"] if base_entry else {"child_processes": 0, "untraced_child_processes": 0}

    # 基準（スタブなし）が失敗したら、何も通さない（比べる元が無い: 第 7 回）
    baseline_failed = None
    why_b = (base_imp.get("stderr") or "").strip()
    if targets and not ok_targets:
        baseline_failed = {"reason": "基準（スタブなし）で import できたモジュールが 0 件です",
                           "rc": base_imp.get("rc"), "stderr_tail": why_b[-800:]}
    elif run_entry and base_entry["timed_out"]:
        baseline_failed = {"reason": "基準（スタブなし）でエントリーが最後まで実行されませんでした（timeout）",
                           "rc": None, "stderr_tail": base_entry["tb"][-800:]}
    elif run_entry and base_entry["rc"] != 0:
        baseline_failed = {"reason": f"基準（スタブなし）のエントリーが終了コード {base_entry['rc']} で終わりました",
                           "rc": base_entry["rc"], "stderr_tail": base_entry["tb"][-800:]}
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
            why = (last.get("stderr") or "").strip()
            fl.append({"m": miss[0], "ok": False, "err": (
                f"{miss[0]} の import の途中で子プロセスが終了した（終了コード {last.get('rc')}）: "
                + (why.splitlines()[-1] if why else "標準エラーなし")), "tb": why[-3000:]})
        if run_entry and base_entry is not None and not base_entry["timed_out"]:
            er = run_entry_with(keys)
            res["<entry>"] = er
            if er["timed_out"] or er["rc"] != base_entry["rc"] or er["stdout_key"] != base_entry["stdout_key"]:
                msg = er["err"]
                if er["timed_out"]:
                    msg = f"エントリーが最後まで実行されなかった（{timeout} 秒で打ち切り）"
                elif er["rc"] != base_entry["rc"]:
                    msg = (f"エントリーが最後まで実行されなかった（終了コード {er['rc']}。基準は {base_entry['rc']}）: "
                           + (er["err"] or "標準エラーなし"))
                else:
                    msg = er["err"] if er["err"] and er["rc"] != 0 else ""
                fl.append({"m": "<entry>", "entry_mismatch": True, "err": msg, "tb": er["tb"]})
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

    # 実行の記録（指定された時だけ）: 基準のエントリー実行の各プロセス（root と子孫）の記録と、渡された記録ファイルを
    # 合算し、各項目に観測の区分を付ける
    from mcp_pystub import trace as _trace
    trace_data = _trace.merge_procs(base_procs) if (record and run_entry) else None
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
        why: list[str] = []
        if any(under(x, q) or under(q, x) for x in base_startup for q in it["prefixes"] + it["block"]) or \
                (obs is not None and obs.get("loaded_at_startup")):
            why.append("起動時（.pth など）に読み込まれるため未確認（スタブの差し込みより前に本物が読み込まれる）")
        if int(base_sum.get("untraced_child_processes", 0)) > 0 or \
                (trace_data is not None and int(trace_data.get("untraced_child_processes", 0)) > 0):
            why.append("子プロセスが起動されたため、その中の使用は確かめていない（記録・差し込みの効かなかった子プロセスがある: "
                       "環境を消した・-E / -I / -S・PYTHONPATH の上書き・python 以外のプログラム、または止められた）")
        elif base_sum.get("child_processes") and not record and \
                not (trace_data is not None and int(trace_data.get("child_processes", 0)) > 0):
            why.append("子プロセスが起動された（その中の記録が無い）")
        if ok and run_entry and obs is not None and obs["state"] in ("not_imported", "import_only") and not why:
            st = "confirmed_run"  # 記録で未実行、かつエントリーの差し替え実行を通った
        elif ok:
            st = "confirmed_import"  # import（と、指定があればエントリーの実行）を通った。実行の記録での確認は無い
        else:
            st = "unconfirmed"
        statuses[name] = {"status": st, "observed": obs, "entry_run_passed": bool(ok and run_entry), "_key": k,
                          "why_not_confirmed_run": why}
    # まとめて確かめた組と項目ごとの状態の食い違い（第 8 回 S1）: 確認済み（実行）の項目だけの組でもう 1 回確かめる
    verified_sets: list[dict] = []
    if active:
        verified_sets.append({"items": [statuses[n]["_key"] for n in statuses if statuses[n]["_key"] in active],
                              "result": "passed", "with_entry": run_entry})
    cand_keys = [v["_key"] for v in statuses.values() if v["status"] == "confirmed_run"]
    if cand_keys and len(cand_keys) < len(active):
        f_s, _r_s, m_s = failures(cand_keys)
        good = not f_s and not m_s
        verified_sets.append({"items": cand_keys, "result": "passed" if good else "failed", "with_entry": True,
                              "for": "確認済み（実行）の項目だけの組",
                              "error": "" if good else ((f_s[0].get("err") or "出力が変わった") if f_s else
                                                        f"import されなかったモジュール: {', '.join(m_s[:3])}")})
        if not good:
            for v in statuses.values():
                if v["status"] == "confirmed_run":
                    v["status"] = "confirmed_import"
                    v["why_not_confirmed_run"].append("確認済み（実行）の項目だけの組では、エントリーの結果が変わった・失敗した")
    for v in statuses.values():
        v.pop("_key", None)
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
        "verified_sets": verified_sets,
        "verified_sets_note": ("確かめた組をそのまま適用してください。一部だけを適用するなら、その組で verify を回し直してください"
                               "（組ごとに結果が変わることがあります）。"),
        "child_processes": base_sum.get("child_processes", 0),
        "untraced_child_processes": base_sum.get("untraced_child_processes", 0),
        "blocked_entry_open": blocked_entry,
        "entry_conditions": (None if not run_entry else {
            "command": ecmd, "cwd": ecwd, "stdin": "DEVNULL", "stdout": "ファイル（バイト列の全体を比べる）",
            "env_added": ["PYTHONPATH の先頭に起動用フォルダ", "MCP_PYSTUB_BOOT"],
            "stdout_head": (base_entry or {}).get("stdout", "")[:2000]}),
        "startup_loaded": sorted(base_startup),
        "excluded_from_import": excluded_entry_shown,
        "traced": trace_data is not None,
        "status_note": ("確認済み（実行）= 実行の記録で未実行（not_imported / import_only）かつ run_entry=True の差し替え実行を通った。"
                        "import 確認済み = スタブに差し替えた import（と、run_entry=True ならエントリーの実行）を通ったが、"
                        "実行の記録での確認は無い / 記録では実行されていた。未確認 = 通らなかった・確かめられなかった。"
                        "記録は動かした入力の範囲だけです。起動用の仕組みが効かない子プロセスと拡張モジュール（.pyd / .so）の中は記録できません（子プロセスは数えて、確認済み（実行）にしません）。"),
        "baseline_failed": baseline_failed,
        "rejected": rejected,
        "unresolved": unresolved,
        "imported_modules": len(ok_targets),
        "baseline_failures": len(targets) - len(ok_targets),
        "baseline_hung": base_hung,
        "run_entry": run_entry,
        "entry_matched": (None if not run_entry or base_entry is None else (
            bool(active) and last_res.get("<entry>", {}).get("stdout_key") == (base_entry or {}).get("stdout_key")
        )),
        "entry_note": entry_note if run_entry else
        "run_entry=False のため、エントリーの実行（出力の一致）は確かめていません。import だけを確かめました。",
        "rounds": rounds,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "python_note": py_note,
        "note": ("" if not excluded_entry_shown else
                 "エントリーを（間接に）import するローカルのモジュールは、エントリーを動かさないため import の対象から外しました: "
                 + ", ".join(excluded_entry_shown) + "。")
                + ("" if not blocked_entry else
                   "import の途中でエントリーのファイルを開こうとしたため止めたモジュール（import できなかった扱い）: "
                   + ", ".join(blocked_entry) + "。")
                + "verify は import（と run_entry=True ならエントリーの 1 回の実行）で確かめるだけで、"
                "全ての実行経路の保証ではありません。ビルド後の実行確認も行ってください。"
                + "import の確認は別の子プロセスで行い、グラフ上のモジュールの直下のコード（import 時のコード）は動きますが、"
                "エントリーのスクリプトは動かしません（エントリーを import するローカルのモジュールは対象から外し、import の途中で"
                "エントリーのファイルを開く操作は監査フックで止めます。Python の `open` を通る読み込みは、別名（短いパス名・`\\?\`・UNC・ハードリンク）でも、ファイルの同一性で見分けて止めます。OS のコピー API（`shutil.copy2` の CopyFile2 など）、ctypes、外部コマンド（`cmd /c type` 等）での読み込みと、それでコピーしたファイル経由の import（zipimport 等）は止められません）。"
                + ("" if not run_entry else
                   "エントリーは、標準出力をファイルにつないだ普段の実行（" + " ".join(ecmd[1:]) + "、作業フォルダ " + ecwd +
                   "）と同じ条件で動かし、標準出力のバイト列の全体（子孫のプロセス・atexit の出力も含む）と終了コードを比べます。"
                   "主な違いは PYTHONPATH の先頭の起動用フォルダと環境変数 MCP_PYSTUB_BOOT の 2 つで（ほかは README の表）、標準入力は空"
                   "（DEVNULL）です。標準エラー・ファイル・通信は比べません。終わった後・timeout の時は孫を含むプロセスの木を止めます。"),
    }
    return out
