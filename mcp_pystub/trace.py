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


class _Recorder:
    def __init__(self, entry_file: str) -> None:
        self.entry_file = os.path.normcase(os.path.abspath(entry_file)) if entry_file else ""
        self.runtime: dict = {}  # code -> True（実行時に呼ばれた）
        self.import_time: set = set()  # import の途中だけで呼ばれたコード

    def _at_import(self, code) -> bool:
        """code の呼び出しが、そのコード自身のパッケージの import の途中（自分のモジュール本体から）か。

        別のパッケージの import の途中に呼ばれた（libconv の import 時に importtime.setup() を呼ぶ等）のは実行として数える。
        エントリー自身のモジュール本体からの呼び出しも実行として数える。
        """
        f = sys._getframe(1)
        while f is not None and f.f_code is not code:
            f = f.f_back
        if f is None:
            return False
        own = str(f.f_globals.get("__name__", "")).split(".")[0]
        g = f.f_back
        while g is not None:
            c = g.f_code
            if c.co_name == "<module>":
                if os.path.normcase(os.path.abspath(c.co_filename)) == self.entry_file:
                    return False
                mod = str(g.f_globals.get("__name__", "")).split(".")[0]
                return mod == own and own != "__main__"
            g = g.f_back
        return False

    def on_start(self, code) -> bool:
        """関数が始まった。True を返したら、このコードの以後の通知は要らない（実行時の呼び出しを見た）。"""
        if not (code.co_flags & CO_OPTIMIZED):
            return True  # モジュール本体・クラス本体（import の結果として modules で分かる）
        if code in self.runtime:
            return True
        if self._at_import(code):
            self.import_time.add(code)
            return False  # 実行時に呼ばれるまで見続ける
        self.runtime[code] = True
        return True

    def result(self) -> dict:
        def rec(c):
            return [c.co_filename, getattr(c, "co_qualname", c.co_name), c.co_firstlineno]
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
            "executed": sorted({tuple(rec(c)) for c in self.runtime}),
            "import_time": sorted({tuple(rec(c)) for c in self.import_time if c not in self.runtime}),
            "modules": mods,
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

    imported = sorted(m for m in trace.get("modules", {}) if m == target or m.startswith(target + "."))
    ex = [x for x in trace.get("executed", []) if inside(x[0])]
    ex = [x for x in ex if x[1].split(".")[0] not in _COMPREHENSIONS or "." in x[1]]
    it = [x for x in trace.get("import_time", []) if inside(x[0])]
    native = any(_has_native(r) for r in roots)
    if ex:
        state = "executed"
    elif not imported:
        state = "not_imported"
    elif native:
        state = "unknown_native"
    else:
        state = "import_only"
    names = sorted({x[1] for x in ex})
    return {"state": state, "functions_executed": len(ex), "top_functions": names[:10],
            "import_time_functions": len(it), "modules_imported": len(imported), "has_native": native,
            "note": ("拡張モジュール（.pyd / .so）の中の実行は記録できません（未実行とは扱いません）" if native else "")}


# ---------------------------------------------------------------------------
# 起動（python -m mcp_pystub.trace）
# ---------------------------------------------------------------------------

USAGE = ("usage: python -m mcp_pystub.trace --out TRACE.json [--append] (script.py | -m module) [args...]\n"
         "対象のプログラムを実際に実行します（副作用が起きえます）。")


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
        target_argv = [args[1]] + args[2:]
        entry_file = ""
    else:
        entry_file = args[0]
        target_argv = args
        sys.path.insert(0, os.path.dirname(os.path.abspath(entry_file)))
    rc = 0
    rec = start(entry_file)
    old_argv = sys.argv
    sys.argv = target_argv
    try:
        if module:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        else:
            runpy.run_path(entry_file, run_name="__main__")
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        rc = 1
    finally:
        sys.argv = old_argv
        data = stop(rec)
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
