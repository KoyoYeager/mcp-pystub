"""実行の記録（第 7 回 第 2 段）。

対象のプログラムを実際に動かし、どのモジュールが import され、どの関数が実行されたかを記録する。
**対象のプログラムを実際に実行する**ので、ファイルの書き込み・削除、通信、機器の操作などの副作用が起きうる。
利用者が自分の管理下（試験用のデータ・テストの実行など）で動かすことを想定する:

    python -m mcp_pystub.trace --out trace.json entry.py 引数...
    python -m mcp_pystub.trace --out trace.json -m pytest tests/      （-m 形式の起動）
    python -m mcp_pystub.trace --out trace.json --append entry.py 別の引数   （複数回の実行を合算）

記録するもの:
- import されたモジュールとそのファイル（終了時の sys.modules）
- 実行された関数（コードオブジェクト: ファイル・qualname・先頭行）。import の途中（どこかのモジュール本体の実行中。
  エントリー自身の本体は除く）にだけ呼ばれたものは import_time として分ける
- Python 3.12 以降は sys.monitoring（関数の開始イベント。実行時に呼ばれたと分かったコードは以後の通知を止める）、
  それ以前は sys.setprofile + threading.setprofile

制限: 子プロセスの中は記録しない。動かした入力の範囲しか分からない。拡張モジュール（.pyd / .so）の中は記録できない。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TRACE_VERSION = 1
CO_OPTIMIZED = 0x0001  # 関数（クラス本体・モジュール本体には付かない）
_COMPREHENSIONS = ("<genexpr>", "<listcomp>", "<dictcomp>", "<setcomp>")


def _owner(frame) -> str:
    """ファイルの無いコード（co_filename が <string> 等: dataclass の生成メソッド・namedtuple・exec）の所属するモジュール。

    実行時のグローバルの __name__ が実在するモジュールならそれ。そうでなければ（namedtuple の __new__ は
    __name__ = 'namedtuple_<型名>'）最初の引数（self / cls）の型の __module__。
    """
    try:
        g = frame.f_globals.get("__name__")
        if isinstance(g, str) and g in sys.modules and g not in ("builtins",):
            return g
        code = frame.f_code
        if code.co_argcount:
            a = frame.f_locals.get(code.co_varnames[0])
            t = a if isinstance(a, type) else type(a)
            m = getattr(t, "__module__", None)
            if isinstance(m, str) and m != "builtins":
                return m
        return g if isinstance(g, str) else ""
    except Exception:  # noqa: BLE001
        return ""


class _Recorder:
    def __init__(self, entry_file: str) -> None:
        self.entry_file = os.path.normcase(os.path.abspath(entry_file)) if entry_file else ""
        self.runtime: dict = {}  # code -> 所属モジュール（ファイルの無いコードだけ。他は ""）
        self.import_time: dict = {}  # import の途中だけで呼ばれたコード -> 所属モジュール
        # 記録を始めた時に既に読み込まれていたモジュール（.pth などで起動時に読み込まれたもの）
        self.startup_modules = sorted(n for n in list(sys.modules) if "." not in n)

    def _frame(self, code):
        f = sys._getframe(2)
        while f is not None and f.f_code is not code:
            f = f.f_back
        return f

    def _at_import(self, f) -> bool:
        """f（関数の実行）が、そのコード自身のパッケージの import の途中（自分のモジュール本体から）か。

        別のパッケージの import の途中に呼ばれた（libconv の import 時に importtime.setup() を呼ぶ等）のは実行として数える。
        エントリー自身のモジュール本体からの呼び出しも実行として数える。エントリーの関数の実行中に遅れて import した
        モジュールの本体から呼ばれたもの（第 8 回 N8: def go(): import xlz.sub）も実行として数える。
        """
        if f is None:
            return False
        own = str(f.f_globals.get("__name__", "")).split(".")[0]
        g = f.f_back
        first = None
        while g is not None:
            c = g.f_code
            if self.entry_file and os.path.normcase(os.path.abspath(c.co_filename)) == self.entry_file:
                if first is None or (c.co_flags & CO_OPTIMIZED):
                    return False  # エントリーの本体から直接、またはエントリーの関数の実行中
            if c.co_name == "<module>" and first is None:
                mod = str(g.f_globals.get("__name__", "")).split(".")[0]
                first = mod == own and own != "__main__"
                if not first:
                    return False
            g = g.f_back
        return bool(first)

    def on_start(self, code) -> bool:
        """関数が始まった。True を返したら、このコードの以後の通知は要らない（実行時の呼び出しを見た）。"""
        if not (code.co_flags & CO_OPTIMIZED):
            return True  # モジュール本体・クラス本体（import の結果として modules で分かる）
        if code in self.runtime:
            return True
        f = self._frame(code)
        owner = _owner(f) if (f is not None and code.co_filename.startswith("<")) else ""
        if self._at_import(f):
            self.import_time[code] = owner
            return False  # 実行時に呼ばれるまで見続ける
        self.runtime[code] = owner
        return True

    def result(self) -> dict:
        def rec(c, owner=""):
            return [c.co_filename, getattr(c, "co_qualname", c.co_name), c.co_firstlineno, owner]
        mods = {}
        for name, m in list(sys.modules.items()):
            f = getattr(m, "__file__", None)
            if isinstance(f, str):
                mods[name] = f
        return {
            "version": TRACE_VERSION,
            "python": sys.version.split()[0],
            "runs": 1,
            "argv": [sys.argv],
            "entry": [self.entry_file],
            "executed": sorted({tuple(rec(c, o)) for c, o in self.runtime.items()}),
            "import_time": sorted({tuple(rec(c, o)) for c, o in self.import_time.items() if c not in self.runtime}),
            "modules": mods,
            "startup_modules": self.startup_modules,
            "child_processes": 0,
            "untraced_child_processes": 0,
        }


def start(entry_file: str = "") -> _Recorder:
    """記録を始める（同じプロセスの中）。"""
    rec = _Recorder(entry_file)
    if sys.version_info >= (3, 12):
        mon = sys.monitoring
        tool = None
        for t in (mon.PROFILER_ID, 5, 4, 3):
            try:
                mon.use_tool_id(t, "mcp_pystub.trace")
                tool = t
                break
            except ValueError:
                continue
        if tool is None:
            raise RuntimeError("sys.monitoring の tool id が空いていません")
        disable = mon.DISABLE

        def cb(code, offset):
            if rec.on_start(code):
                return disable
            return None
        mon.register_callback(tool, mon.events.PY_START, cb)
        mon.set_events(tool, mon.events.PY_START)
        rec._tool = tool  # type: ignore[attr-defined]
    else:
        import threading

        def prof(frame, event, arg):
            if event == "call":
                rec.on_start(frame.f_code)
        sys.setprofile(prof)
        threading.setprofile(prof)
        rec._prof = prof  # type: ignore[attr-defined]
    return rec


def stop(rec: _Recorder) -> dict:
    if sys.version_info >= (3, 12):
        tool = getattr(rec, "_tool", None)
        if tool is not None:
            sys.monitoring.set_events(tool, 0)
            sys.monitoring.register_callback(tool, sys.monitoring.events.PY_START, None)
            sys.monitoring.free_tool_id(tool)
    else:
        import threading
        sys.setprofile(None)
        threading.setprofile(None)
    return rec.result()


def merge(a: dict | None, b: dict) -> dict:
    """複数回の実行（引数違い）の記録を合算する。"""
    if not a:
        return b
    out = dict(a)
    out["runs"] = int(a.get("runs", 1)) + int(b.get("runs", 1))
    out["argv"] = list(a.get("argv", [])) + list(b.get("argv", []))
    out["entry"] = list(a.get("entry", [])) + list(b.get("entry", []))
    ex = {tuple(x) for x in a.get("executed", [])} | {tuple(x) for x in b.get("executed", [])}
    it = ({tuple(x) for x in a.get("import_time", [])} | {tuple(x) for x in b.get("import_time", [])}) - ex
    out["executed"] = sorted(ex)
    out["import_time"] = sorted(it)
    mods = dict(a.get("modules", {}))
    mods.update(b.get("modules", {}))
    out["modules"] = mods
    out["startup_modules"] = sorted(set(a.get("startup_modules", [])) | set(b.get("startup_modules", [])))
    for k in ("child_processes", "untraced_child_processes"):
        out[k] = int(a.get(k, 0)) + int(b.get(k, 0))
    return out


def load(paths: list[str] | str) -> dict | None:
    """記録ファイル（複数なら合算）を読む。"""
    if isinstance(paths, str):
        paths = [paths]
    acc = None
    for p in paths:
        acc = merge(acc, json.loads(Path(p).read_text(encoding="utf-8")))
    return acc


# ---------------------------------------------------------------------------
# 観測の区分
# ---------------------------------------------------------------------------

def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _has_native(root: Path) -> bool:
    try:
        if root.is_dir():
            return any(f.suffix in (".pyd", ".so") for f in root.rglob("*") if "__pycache__" not in f.parts)
        return root.suffix in (".pyd", ".so")
    except OSError:
        return False


def observe(trace: dict, target: str, site_packages: list[str]) -> dict:
    """パッケージ（またはサブモジュール）target の観測の区分。

    not_imported: 実行時に import されなかった / import_only: import されたが関数は実行されなかった
    executed: 関数が実行された（件数と代表的な名前）/ unknown_native: 拡張モジュールを含み、関数の実行が記録に無い
    （拡張モジュールの中の実行は記録できないので、未実行とは扱わない）
    """
    parts = target.split(".")
    roots: list[Path] = []
    for sp in site_packages:
        base = Path(sp).joinpath(*parts)
        for c in (base, base.with_name(base.name + ".py")):
            if c.exists():
                roots.append(c)
        if base.parent.is_dir():
            for f in base.parent.glob(parts[-1] + ".*"):
                if f.suffix in (".pyd", ".so"):
                    roots.append(f)
    prefixes = [_norm(str(r)) for r in roots]

    def inside(path: str) -> bool:
        p = _norm(path)
        return any(p == x or p.startswith(x + os.sep) for x in prefixes)

    def belongs(x) -> bool:
        if str(x[0]).startswith("<"):
            o = x[3] if len(x) > 3 else ""
            return bool(o) and (o == target or o.startswith(target + "."))
        return inside(x[0])

    imported = sorted(m for m in trace.get("modules", {}) if m == target or m.startswith(target + "."))
    ex = [x for x in trace.get("executed", []) if belongs(x)]
    ex = [x for x in ex if x[1].split(".")[0] not in _COMPREHENSIONS or "." in x[1]]
    it = [x for x in trace.get("import_time", []) if belongs(x)]
    native = any(_has_native(r) for r in roots)
    at_startup = target.split(".")[0] in set(trace.get("startup_modules", []))
    if ex:
        state = "executed"
    elif at_startup:
        state = "loaded_at_startup"  # 記録を始める前（起動時の .pth など）に読み込まれていた: 未実行とは扱わない
    elif not imported:
        state = "not_imported"
    elif native:
        state = "unknown_native"
    else:
        state = "import_only"
    names = sorted({x[1] for x in ex})
    return {"state": state, "functions_executed": len(ex), "top_functions": names[:10],
            "import_time_functions": len(it), "modules_imported": len(imported), "has_native": native,
            "loaded_at_startup": at_startup,
            "note": ("起動時（.pth など）に読み込まれるため、記録では確かめられません（未実行とは扱いません）" if at_startup
                     else "拡張モジュール（.pyd / .so）の中の実行は記録できません（未実行とは扱いません）" if native else "")}


# ---------------------------------------------------------------------------
# 起動（python -m mcp_pystub.trace）
# ---------------------------------------------------------------------------

BOOT = r'''
# mcp-pystub の起動時の差し込み（verify・実行の記録の子孫のプロセスにも効かせる）。MCP_PYSTUB_BOOT の設定を読む
def _pystub_boot():
    import json, os, sys
    path = os.environ.get("MCP_PYSTUB_BOOT")
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        sys.path[:] = [x for x in sys.path if os.path.normcase(os.path.abspath(x or ".")) != os.path.normcase(here)]
    except Exception:
        pass
    if not path:
        return
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception:
        return
    import importlib.abc, importlib.machinery, importlib.util
    STUBS, PREFIXES, BLOCK = cfg.get("stubs", {}), cfg.get("prefixes", []), cfg.get("block", [])
    entry = cfg.get("entry") or ""
    nentry = os.path.normcase(os.path.abspath(entry)) if entry else ""
    stem = os.path.splitext(os.path.basename(entry))[0] if entry else ""
    guard = bool(cfg.get("entry_guard"))

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
            if guard and stem and name.rpartition(".")[2] == stem:
                sp = importlib.machinery.PathFinder.find_spec(name, path)
                if sp is not None and sp.origin and os.path.normcase(os.path.abspath(sp.origin)) == nentry:
                    raise ImportError(f"{name}: import だけの確認ではエントリーのスクリプトを import しません "
                                      "(excluded by verify)", name=name)
            return None

    if STUBS or BLOCK or PREFIXES or guard:
        sys.meta_path.insert(0, _Finder())
    for d in cfg.get("extra_path", []):
        if d not in sys.path:
            sys.path.append(d)
    root = os.environ.get("MCP_PYSTUB_ROOT") == "1"
    os.environ["MCP_PYSTUB_ROOT"] = "0"  # これより後に起動する子孫は root ではない
    loaded = [p for p in cfg.get("watch", []) if any(_under(m, p) for m in list(sys.modules))]
    rec = None
    trm = None
    if cfg.get("trace_mod"):
        spec = importlib.util.spec_from_file_location("_pystub_trace", cfg["trace_mod"])
        trm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(trm)
        rec = trm.start(entry)
    sys._pystub_boot = {"root": root, "startup_loaded": loaded, "rec": rec, "trm": trm}
    if root:
        return
    pd = cfg.get("proc_dir")
    if not pd:
        return
    os.makedirs(pd, exist_ok=True)
    pf = os.path.join(pd, f"proc_{os.getpid()}.json")
    info = {"pid": os.getpid(), "startup_loaded": loaded, "trace": None}
    with open(pf, "w", encoding="utf-8") as fh:
        json.dump(info, fh)

    def _done():
        if rec is not None:
            try:
                info["trace"] = trm.stop(rec)
            except Exception:
                pass
        with open(pf, "w", encoding="utf-8") as fh:
            json.dump(info, fh)
    import atexit
    atexit.register(_done)


_pystub_boot()
del _pystub_boot
import sys as _s
_me = _s.modules.pop("sitecustomize", None)
try:
    import sitecustomize  # noqa: F401  元の sitecustomize があれば続けて読む
except ImportError:
    if _me is not None:
        _s.modules["sitecustomize"] = _me
'''


def shutil_rm(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def install_boot(proc_dir: Path, cfg: dict) -> dict:
    """子孫のプロセスに差し込みを引き継ぐ: 一時フォルダの sitecustomize.py を PYTHONPATH の先頭に置く。"""
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="pystub_boot_"))
    (d / "sitecustomize.py").write_text(BOOT, encoding="utf-8")
    (d / "boot.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    saved = {k: os.environ.get(k) for k in ("PYTHONPATH", "MCP_PYSTUB_BOOT", "MCP_PYSTUB_ROOT")}
    pp = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(d) + (os.pathsep + pp if pp else "")
    os.environ["MCP_PYSTUB_BOOT"] = str(d / "boot.json")
    os.environ["MCP_PYSTUB_ROOT"] = "0"
    return {"dir": d, "saved": saved}


def uninstall_boot(b: dict) -> None:
    for k, v in b["saved"].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil_rm(b["dir"])


def read_children(proc_dir: Path) -> list[dict]:
    out = []
    if proc_dir.is_dir():
        for f in sorted(proc_dir.glob("proc_*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                out.append({"pid": f.stem, "trace": None, "startup_loaded": []})
    return out


def merge_children(data: dict, proc_dir: Path) -> dict:
    """子孫のプロセスの記録を合算する（記録の取れなかった子孫は数える）。"""
    kids = read_children(proc_dir)
    runs = data.get("runs", 1)
    for k in kids:
        if k.get("trace"):
            data = merge(data, k["trace"])
        else:
            data["untraced_child_processes"] = int(data.get("untraced_child_processes", 0)) + 1
    data["runs"] = runs
    data["child_processes"] = len(kids)
    shutil_rm(proc_dir)
    return data


USAGE = ("usage: python -m mcp_pystub.trace --out TRACE.json [--append] (script.py | -m module) [args...]\n"
         "対象のプログラムを実際に実行します（副作用が起きえます）。")


def _run_script(path: str) -> None:
    """python <スクリプト> と同じ形で実行する（runpy.run_path は sys.argv[0] を書き換えるので使わない）。"""
    import builtins
    import importlib.machinery
    import types
    with open(path, "rb") as fh:
        code = compile(fh.read(), path, "exec")
    mod = types.ModuleType("__main__")
    mod.__file__ = path
    mod.__builtins__ = builtins
    mod.__loader__ = importlib.machinery.SourceFileLoader("__main__", path)
    mod.__spec__ = None
    mod.__cached__ = None
    saved = sys.modules.get("__main__")
    sys.modules["__main__"] = mod
    try:
        exec(code, mod.__dict__)
    finally:
        if saved is not None:
            sys.modules["__main__"] = saved


def main(argv: list[str] | None = None) -> int:
    import runpy
    args = list(sys.argv[1:] if argv is None else argv)
    out = None
    append = False
    while args and args[0].startswith("--"):
        a = args.pop(0)
        if a == "--out" and args:
            out = args.pop(0)
        elif a == "--append":
            append = True
        else:
            print(USAGE, file=sys.stderr)
            return 2
    if not out or not args:
        print(USAGE, file=sys.stderr)
        return 2
    module = None
    if args[0] == "-m" and len(args) >= 2:
        module = args[1]
        target_argv = [args[1]] + args[2:]  # runpy が sys.argv[0] をモジュールのファイルに置き換える（普段の -m と同じ）
        entry_file = ""
    else:
        # 普段の python entry.py と同じにする: __file__ は絶対パス、sys.argv[0] は渡したまま、sys.path[0] はスクリプトの
        # あるフォルダ（-m mcp_pystub.trace が入れた作業フォルダは置き換える）
        entry_file = os.path.abspath(args[0])
        target_argv = args
        sys.path[0] = os.path.dirname(entry_file)
    # 子孫のプロセス（multiprocessing の spawn・subprocess で起動した python）にも記録を引き継ぐ
    child_dir = Path(out).with_name(Path(out).name + ".procs")
    shutil_rm(child_dir)
    boot = install_boot(child_dir, {"stubs": {}, "prefixes": [], "block": [], "watch": [], "entry": entry_file,
                                    "trace_mod": str(Path(__file__).resolve()), "proc_dir": str(child_dir)})
    rc = 0
    rec = start(entry_file)
    old_argv = sys.argv
    sys.argv = target_argv
    try:
        if module:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        else:
            _run_script(entry_file)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        rc = 1
    finally:
        sys.argv = old_argv
        data = stop(rec)
        data = merge_children(data, child_dir)
        uninstall_boot(boot)
        data["rc"] = [rc]
        prev = None
        if append and Path(out).exists():
            try:
                prev = json.loads(Path(out).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prev = None
        if prev:
            data = merge(prev, data)
            data["rc"] = list(prev.get("rc", [])) + [rc]
        Path(out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
