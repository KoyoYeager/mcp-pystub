# ruff: noqa: E501  (HTML と CSS の文字列は 1 行が長い)

"""README の冒頭の GIF(使い方の流れ: 登録 → 頼む → Claude の作業(候補探し・確認・適用・ビルド・元に戻す)→ 結果 → 止まるスタブ)を作る(英語版 demo.gif と日本語版 demo.ja.gif)。

GIF は、本物の Claude Code の会話の記録から描く。作り話の画面は使わない。

使い方(リポジトリの一番上で。ffmpeg が PATH に要る。--ffmpeg でも渡せる):
    # 1. 記録した会話(claude -p --output-format stream-json --verbose)から、画面に出す値を取り出す(言語ごと)
    uv run python tools/demo_gif.py extract --conv DIR/ja/conv.jsonl --project C:/work/demo/ja --out DIR/record_ja.json
    # 2. 保存した記録から GIF を作る(DIR に record_ja.json・record_en.json・builds_ja.json・builds_en.json・safety/ がある)
    uv run --with pillow python tools/demo_gif.py build --data DIR
    uv run --with pillow python tools/demo_gif.py build --data DIR --out DIR --keep-frames DIR --keep-html DIR --lang ja

DIR に置くもの:
  record_<lang>.json  extract が作る。ツールの呼び出しと結果の要点・Claude の返事・所要時間・費用。
  builds_<lang>.json  Claude 自身がビルドした exe の大きさと、その exe を Claude が動かして出た出力(記録の Bash の出力から)
                      {"base": {"bytes", "stdout"}, "after": {"bytes", "stdout"}}。
  safety/plot.json・safety/plot_entry.py  外した部分に触れると理由を出して止まる場面の、以前に記録した出力
                      (スタブを当てた venv で Signal.plot() を呼ぶエントリーを動かし、必ず復元して確かめた結果)。

build は、場面(登録・頼む・Claude の作業・ビルド・安全網)ごとにコマの状態(打ち込み・字幕・強調・拡大)を作り、
HTML にして Edge か Chrome の headless で撮り、下に進み具合のバーを足して、ffmpeg の palettegen / paletteuse で GIF にする
(880x496、10 fps、3 MB 以下を確かめる)。字幕の数字(サイズ)は記録から読む(手で書かない)。
画面のパスは、作業場所からの相対(main.py、.venv/Lib/site-packages)に置き換えて表示する。値そのものは変えない。
個人の情報(ドライブ付きのパス・ユーザー名)が HTML に残っていたら止まる。
Pillow・ffmpeg は作るときだけ使う(mcp-pystub の依存には入れない)。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W, H = 880, 496  # GIF の大きさ(px)
FPS = 10
FONT_PX, LINE_PX, PAD_X, PAD_Y = 14, 19, 18, 12
BAR_PX = 6  # 進み具合のバー
MAX_ZOOM = 1.4
MAX_BYTES = 3 * 1024 * 1024
BAR_COLS = 56  # 結果の棒の最大の長さ(桁)
WRAP_COLS = 92

# 色(暗いターミナル)
CSS_CLASSES = {
    "tool": "#dcdcaa",
    "cmd": "#9cdcfe",
    "arg": "#9cdcfe",
    "str": "#ce9178",
    "num": "#b5cea8",
    "kw": "#c586c0",
    "lit": "#569cd6",
    "ok": "#23d18b",
    "bad": "#f14c4c",
    "dim": "#808080",
    "key": "#9cdcfe",
    "bar0": "#f14c4c",
    "bar1": "#23d18b",
}


TEXT = {
    "en": {
        "prompt": "Use mcp-pystub to make the exe of main.py smaller. The venv is .venv. You can run it to check.",
        "cap_register": "1. Add it to Claude Code (once)",
        "cap_ask": "2. Ask Claude",
        "cap_claude": "Claude finds candidates, checks by running, applies the stubs, builds, and restores the venv",
        "cap_build": "Claude builds it: <em>{base} MB -> {after} MB</em>, same output",
        "cap_stop": "If you do use a removed part, it does not break silently: it <em>stops and says why</em>",
        "header": "# Claude Code, in the project folder",
        "reply_note": "(Claude's reply, first sentence)",
        "build_head": "# exe built by Claude (PyInstaller onefile), as recorded",
        "files": "{n} stub files",
        "excluded": "excludes {first} and {more} more",
        "of": "(was {a} -> {b})",
        "more_recs": "(+{n} more candidates)",
        "installed": "installed size, not exe savings",
        "unconfirmed": "unconfirmed",
        "gloss": {
            "confirmed_run": "confirmed by running",
            "confirmed_import": "confirmed by import (and one run of the entry)",
            "unconfirmed": "unconfirmed",
        },
        "overlap": "(English gloss: python_path overlaps the project)",
        "entry_matched": "entry_matched: ",
        "matched_note": "(stdout `{out}` is the same as without the stubs)",
        "exe_label": "exe",
        "before": "before",
        "after": "after ",
        "output": "output",
        "match": "match: ",
        "plot_head": "# run an entry that calls Signal.plot() in a venv with the stub applied (recorded earlier)",
        "exit_code": "exit code: ",
        "g_stop": "(English gloss: stub of asammdf.gui; Signal.plot() was called, so it stopped with exit code {rc})",
    },
    "ja": {
        "prompt": "mcp-pystub で main.py の exe を小さくして。venv は .venv。動かして確かめていいよ。",
        "cap_register": "1. 登録する(最初の 1 回)",
        "cap_ask": "2. 頼む",
        "cap_claude": "Claude が候補探し・確認・適用・ビルド・元に戻すまでやる",
        "cap_build": "Claude がビルド: <em>{base} MB → {after} MB</em>、出力は同じ",
        "cap_stop": "使っていた場合は、黙って壊れず<em>理由を出して止まる</em>",
        "header": "# Claude Code(プロジェクトのフォルダで)",
        "reply_note": "",
        "build_head": "# Claude がビルドした exe(PyInstaller onefile)の記録",
        "files": "スタブ {n} ファイル",
        "excluded": "外れるもの: {first} ほか {more} 件",
        "of": "(元 {a} → {b})",
        "more_recs": "(ほか {n} 件)",
        "installed": "インストール済みの大きさ",
        "unconfirmed": "未確認",
        "gloss": {
            "confirmed_run": "確認済み(実行)",
            "confirmed_import": "import 確認済み",
            "unconfirmed": "未確認",
        },
        "overlap": "",
        "entry_matched": "entry_matched: ",
        "matched_note": "(標準出力 `{out}` がスタブ無しと同じ)",
        "exe_label": "exe",
        "before": "適用前",
        "after": "適用後",
        "output": "出力",
        "match": "一致: ",
        "plot_head": "# スタブを当てた venv で、Signal.plot() を呼ぶエントリーを動かす(以前に記録した出力)",
        "exit_code": "終了コード: ",
        "g_stop": "",
    },
}

CSS = (
    f"""*{{box-sizing:border-box}}
html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#1e1e1e}}
#view{{position:absolute;left:0;top:0;width:{W}px;height:{H - BAR_PX}px;overflow:hidden}}
#term{{position:absolute;left:0;top:0;transform-origin:0 0}}
pre{{position:relative;margin:0;padding:{PAD_Y}px {PAD_X}px;color:#d4d4d4;font-size:{FONT_PX}px;line-height:{LINE_PX}px;
font-family:"Cascadia Mono",Consolas,"BIZ UDGothic","MS Gothic",monospace;white-space:pre}}
.ln{{height:{LINE_PX}px;transition:none}}
.dimrow{{opacity:.28}}
.c1{{display:inline-block;width:1ch;text-align:center;overflow:hidden;vertical-align:bottom}}
.c2{{display:inline-block;width:2ch;text-align:center;vertical-align:bottom}}
.c2 i{{font-style:normal;font-family:"BIZ UDGothic","MS Gothic","Yu Gothic",monospace}}
.cur{{display:inline-block;width:1ch;background:#d4d4d4;color:#1e1e1e}}
.box{{position:absolute;border:2px solid #f5c542;border-radius:4px;box-shadow:0 0 10px rgba(245,197,66,.55)}}
.cap{{position:absolute;left:50%;bottom:{BAR_PX + 22}px;transform:translateX(-50%);white-space:nowrap;
padding:8px 22px;border-radius:24px;background:#f6f8fa;color:#1f2328;font:700 19px "Segoe UI","Yu Gothic UI","Meiryo",sans-serif;
box-shadow:0 2px 14px rgba(0,0,0,.45)}}
.cap em{{font-style:normal;color:#1f6feb}}
"""
    + "".join(f".{k}{{color:{v}}}\n" for k, v in CSS_CLASSES.items())
)


def cell_len(s: str) -> int:
    """端末での桁の数(全角は 2 桁)。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


# ---------------------------------------------------------------- 行
Seg = tuple[str, str]  # (文字, 色の class。"" は既定)


class Line:
    def __init__(self, *segs: str | Seg):
        self.segs: list[Seg] = [(s, "") if isinstance(s, str) else s for s in segs]

    @property
    def plain(self) -> str:
        return "".join(t for t, _c in self.segs)

    def html(self) -> str:
        out = []
        for text, cls in self.segs:
            body = "".join(
                html.escape(ch)
                if ord(ch) < 128
                else f'<span class="c2"><i>{html.escape(ch)}</i></span>'
                if cell_len(ch) == 2
                else f'<span class="c1">{html.escape(ch)}</span>'
                for ch in text
            )
            out.append(f'<span class="{cls}">{body}</span>' if cls else body)
        return "".join(out)


def col_of(line: Line, sub: str) -> tuple[int, int]:
    """sub の桁の位置と幅(全角は 2 桁)。"""
    p = line.plain
    return cell_len(p[: p.index(sub)]), cell_len(sub)


def wrap(text: str, width: int) -> list[str]:
    """桁の数で折り返す(表示だけ。文字は変えない)。"""
    out, cur, n = [], "", 0
    for ch in text:
        w = cell_len(ch)
        if n + w > width:
            k = cur.rfind(" ")
            if k > len(cur) // 2:  # 単語の途中で切らない(空白があればそこで)
                out.append(cur[:k])
                cur = cur[k + 1 :]
            else:
                out.append(cur)
                cur = ""
            n = cell_len(cur)
        cur += ch
        n += w
    if cur:
        out.append(cur)
    return out


TOKEN = re.compile(r'(?P<str>"[^"]*"?)|(?P<lit>\b(?:True|False|None)\b)|(?P<num>\b\d+(?:\.\d+)?\b)|(?P<arg>\b\w+(?==))|(?P<tool>^\w+(?=\())')
PYTOKEN = re.compile(r'(?P<dim>#.*$)|(?P<str>"[^"]*")|(?P<kw>\b(?:import|from|as)\b)|(?P<num>\b\d+(?:\.\d+)?\b)')


def colorize(text: str, pattern: re.Pattern[str]) -> list[Seg]:
    segs: list[Seg] = []
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            segs.append((text[pos : m.start()], ""))
        segs.append((m.group(), m.lastgroup or ""))
        pos = m.end()
    if pos < len(text):
        segs.append((text[pos:], ""))
    return segs


def call_lines(parts: list[str], n: int | None = None, cursor: bool = False) -> list[Line]:
    """ツールの呼び出しを、先頭から n 文字まで打ち込んだ状態の行にする(2 行目以降は字下げ)。"""
    total = "\n".join(parts)
    shown = total if n is None else total[:n]
    lines = []
    for i, text in enumerate(shown.split("\n")):
        lines.append(Line(("> " if i == 0 else "  ", "dim"), *colorize(text, TOKEN)))
    if cursor:
        lines[-1].segs.append((" ", "cur"))
    return lines


# ---------------------------------------------------------------- コマ
@dataclass
class State:
    lines: list[Line]
    caption: str = ""
    zoom: float = 0.0  # 拡大の進み(0 = 等倍、1 = fit_cols の桁が画面に収まる大きさ。上限 MAX_ZOOM)
    zoom_row: int = 0  # 拡大したとき、この行を画面の上に置く
    fit_cols: int = 0  # 0 のときは行の幅から決める
    max_zoom: float = MAX_ZOOM
    dim_except: tuple[int, ...] | None = None
    boxes: list[tuple[int, int, int]] = field(default_factory=list)  # (行, 桁, 桁の数)


def page(s: State) -> str:
    rows = []
    for i, ln in enumerate(s.lines):
        dim = s.dim_except is not None and i not in s.dim_except
        rows.append(f'<div class="ln{" dimrow" if dim else ""}">{ln.html() or " "}</div>')
    boxes = []
    for row, col, width in s.boxes:
        top = PAD_Y + row * LINE_PX - 2
        boxes.append(
            f'<div class="box" style="left:calc({PAD_X - 4}px + {col}ch);top:{top}px;width:calc({width}ch + 8px);height:{LINE_PX + 4}px"></div>'
        )
    script = ""
    fit = s.fit_cols or max(cell_len(ln.plain) for ln in s.lines) + 1
    probe = f'<span id="probe" style="position:absolute;left:{PAD_X}px;top:0;width:{fit}ch"></span>'
    if s.zoom > 0:
        # 拡大の大きさは、描いた字の幅で決める(字体の幅を前もって決め打ちしない)
        script = (
            "<script>const t=document.getElementById('term'),p=document.getElementById('probe');"
            f"const z=Math.min({s.max_zoom},({W}-16)/p.getBoundingClientRect().right);"
            f"const sc=1+(z-1)*{s.zoom:.3f};const ty=-{s.zoom:.3f}*(sc*({PAD_Y}+{s.zoom_row}*{LINE_PX})-10);"
            "t.style.transform='translateY('+ty+'px) scale('+sc+')';</script>"
        )
    cap = f'<div class="cap">{s.caption}</div>' if s.caption else ""
    return (
        f'<!doctype html><meta charset="utf-8"><style>{CSS}</style><body><div id="view">'
        f'<div id="term"><pre>' + "".join(rows) + "".join(boxes) + probe + f"</pre></div></div>{cap}{script}</body>"
    )




# ---------------------------------------------------------------- 記録(Claude Code の stream-json)から値を取り出す
def need(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"保存した出力が想定と違う: {msg}")


def _result_text(content) -> str:
    return content if isinstance(content, str) else " ".join(i.get("text", "") for i in content)


def _load_mcp_result(x: str, src: Path):
    """MCP ツールの結果。長いと Claude Code が別ファイルに保存するので、同じフォルダに写したものを読む。"""
    if x.startswith("Error: result (") and "tool-results" in x:
        m = re.search(r"tool-results[\\/]([\w.\-]+\.txt)", x)
        need(bool(m) and (src / m.group(1)).exists(), f"長い結果のファイルが {src} に無い: {m.group(1) if m else x[:80]}")
        x = (src / m.group(1)).read_text(encoding="utf-8")
    d = json.loads(x)
    if isinstance(d, list):  # [{"type": "text", "text": "..."}]
        d = json.loads(d[0]["text"])
    return d


def _summarize(name: str, d: dict) -> dict:
    if name == "analyze":
        return {
            "recs": d["summary"]["recommendations"],
            "hints": [
                {k: h[k] for k in ("submodule", "target_package", "confidence", "estimated_savings_mb")}
                for h in d["conditional_submodule_hints"]
            ],
        }
    if name == "verify":
        return {
            "checked": d["checked"], "passed": d["passed"],
            "statuses": {k: v["status"] for k, v in d["statuses"].items()},
            "entry_matched": d["entry_matched"], "run_entry": d["run_entry"], "elapsed_s": d["elapsed_s"],
            "rejected": [{"item": r["item"], "error": r.get("error", "")} for r in d["rejected"]],
            "stdout_head": d.get("entry_conditions", {}).get("stdout_head", ""),
        }  # fmt: skip
    if name == "generate_submodule":
        keys = ("submodule", "original_size_bytes", "stub_size_bytes", "eliminated_packages")
        return {k: d[k] for k in keys} | {"stub_files": len(d["files"])}
    if name == "generate":
        return {k: d[k] for k in ("package", "original_file_count", "stub_file_count", "stub_total_bytes")}
    return {}


def cmd_extract(a: argparse.Namespace) -> int:
    conv = Path(a.conv)
    src = conv.parent
    steps: list[dict] = []
    by_id: dict[str, dict] = {}
    meta: dict = {}
    for raw in conv.read_text(encoding="utf-8").splitlines():
        d = json.loads(raw)
        if d.get("type") == "assistant":
            for c in d["message"]["content"]:
                if c["type"] == "tool_use":
                    st = {"kind": "use", "name": c["name"], "input": c["input"]}
                    if c["name"] == "Bash":
                        st["input"] = {"description": c["input"].get("description", "")}  # コマンドは残さない(パスが入る)
                    steps.append(st)
                    by_id[c["id"]] = st
                elif c["type"] == "text":
                    steps.append({"kind": "text", "text": c["text"]})
        elif d.get("type") == "user":
            for c in d["message"]["content"]:
                if c.get("type") == "tool_result" and c["tool_use_id"] in by_id:
                    st = by_id[c["tool_use_id"]]
                    if st["name"].startswith("mcp__pystub__"):
                        short = st["name"].split("__")[-1]
                        st["summary"] = _summarize(short, _load_mcp_result(_result_text(c["content"]), src))
        elif d.get("type") == "result":
            # バックグラウンドの処理が終わるたびに会話が続き、result が複数出ることがある(費用は積算。最後のものを使う)
            keys = ("subtype", "num_turns", "total_cost_usd", "duration_ms", "is_error", "session_id", "result")
            meta = {k: d.get(k) for k in keys} | {"results": meta.get("results", 0) + 1}
    need(meta.get("subtype") == "success", "会話が success で終わっていない")
    out = {"project": a.project, "conv": conv.name, "meta": meta, "steps": steps}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    names = [s["name"] for s in steps if s["kind"] == "use"]
    print(f"{a.out}: ステップ {len(steps)} 件、ツール {len(names)} 回 ({', '.join(names)})")
    return 0


# ---------------------------------------------------------------- 画面に出す値
PERSONAL = re.compile(r"[A-Za-z]:[\\/]|[\\/]Users[\\/]|hunte|AppData|@", re.I)


class Data:
    def __init__(self, d: Path, lang: str):
        def load(name: str):
            return json.loads((d / name).read_text(encoding="utf-8"))

        self.rec = load(f"record_{lang}.json")
        self.builds = load(f"builds_{lang}.json")
        self.safety = load("safety/plot.json")
        self.plot_entry = (d / "safety" / "plot_entry.py").read_text(encoding="utf-8").splitlines()
        self.project = self.rec["project"].replace("\\", "/").rstrip("/")
        self.old_scenario = str(Path(self.safety["call"]["entry_point"]).parent.parent)

    def rel(self, p: str) -> str:
        """作業場所の中なら相対に、外なら末尾の 2 つだけにする(ドライブやユーザー名を出さない)。"""
        q = p.replace("\\", "/")
        if q.lower().startswith(self.project.lower() + "/"):
            return q[len(self.project) + 1 :]
        return ".../" + "/".join(q.split("/")[-2:])

    def clean(self, s: str) -> str:
        """文の中のパスを相対にする(安全網の記録は、以前のシナリオのフォルダを取り除く)。"""
        for r in {self.old_scenario, self.old_scenario.replace("\\", "/")}:
            s = s.replace(r + "\\", "").replace(r + "/", "")
        return re.sub(r"[A-Za-z]:[\\/][^\s）)\"'`]*", lambda m: self.rel(m.group()), s)

    @property
    def steps(self) -> list[dict]:
        return self.rec["steps"]


def first_sentence(text: str) -> str:
    t = re.sub(r"[*`]", "", text.strip()).replace("\n", " ")
    m = re.search(r"[。.!?](?=\s|$)", t)
    return t[: m.end()] if m else t


def mb(n: float) -> str:
    return f"{n / 1_000_000:.1f}"


# ---------------------------------------------------------------- 場面
ROWS = 21  # ログとして見せる行数(下に字幕が重なる)
LOGWRAP = 94
Event = tuple[list[Line], "tuple[int, str] | None"]  # (足す行, 強調する(足す行の中の番号, 文字列))


def tool_lines(D: Data, st: dict) -> list[Line]:
    """● ツール名(主な引数)。長ければ引数ごとに折る。"""
    name = st["name"].split("__")[-1]
    inp = st["input"]
    args: list[str] = []
    for k in ("entry_point", "python_path", "package_name", "parent_package", "submodule", "packages", "submodules", "run_entry", "record"):
        if k in inp:
            v = D.rel(inp[k]) if k in ("entry_point", "python_path") else inp[k]
            args.append(f"{k}={str(v) if isinstance(v, bool) else json.dumps(v, ensure_ascii=False)}")
    one = f"{name}({', '.join(args)})"
    texts = [one] if cell_len(one) <= LOGWRAP else [f"{name}({args[0]},", *[f"  {x}," for x in args[1:-1]], f"  {args[-1]})"]
    out = []
    for i, tx in enumerate(texts):
        segs = [(name, "tool"), *colorize(tx[len(name) :], TOKEN)] if i == 0 else colorize(tx, TOKEN)
        out.append(Line(("● " if i == 0 else "  ", "ok"), *segs))
    return out


def res(*segs: str | Seg) -> Line:
    return Line(("  └ ", "dim"), *segs)


def events_of(D: Data, t: dict) -> list[Event]:
    """記録の時間順から、画面に足す出来事を作る。ビルドの Bash は見せない(場面 4 で見せる)。"""
    steps = D.steps
    ev: list[Event] = []
    text_ix = [i for i, s in enumerate(steps) if s["kind"] == "text"]
    verifies = [i for i, s in enumerate(steps) if s["kind"] == "use" and s["name"].endswith("verify")]
    ok_verify = next((i for i in verifies if steps[i]["summary"]["entry_matched"]), None)
    # 返事は、最初の verify の直前・通った verify の直後・最後の 3 か所だけ見せる(ほかは省く)
    show_at = {text_ix[-1]} | {i for i in text_ix if re.search(r"\d\s*MB", first_sentence(steps[i]["text"]))}
    for j in (verifies[0] - 1 if verifies else None, ok_verify + 1 if ok_verify is not None else None):
        if j in text_ix:
            show_at.add(j)
    last_text = text_ix[-1]
    for i, st in enumerate(steps):
        if st["kind"] == "text":
            if i in show_at:
                sentence = D.clean(first_sentence(st["text"]))
                ev.append(([Line(("● ", "ok"), ln) if k == 0 else Line("  ", ln) for k, ln in enumerate(wrap(sentence, LOGWRAP - 2)[:3])], None))
            continue
        if st["name"] == "Bash":
            desc = st["input"]["description"]
            if re.search(r"build|apply|restore|copy|ビルド|適用|復元|写", desc, re.I):  # 適用・ビルド・元に戻す(と回り道)だけ見せる
                ev.append(([Line(("● ", "ok"), ("Bash", "tool"), (f"({desc})", "dim"))], None))
            continue
        if not st["name"].startswith("mcp__pystub__"):
            continue
        name, s = st["name"].split("__")[-1], st["summary"]
        head = tool_lines(D, st)
        if name == "analyze":
            m = re.match(r"\[(\w+)\] (\S+) を.*?(\S+) を排除できます（約 ([\d.]+) MB", s["recs"][0])
            need(bool(m), "analyze の推奨の先頭が想定の形でない")
            conf, sub, target, size = m.groups()
            need(any(h["submodule"] == sub and h["target_package"] == target for h in s["hints"]), "推奨の先頭が hint に無い")
            key, more = f"{sub}=>{target}", len(s["recs"]) - 1
            tail = res(
                (f"[{conf}] ", "ok"), (key, "str"), f"  {size} MB", (f"  ({t['installed']})", "dim"),
                (f"  {t['more_recs'].format(n=more)}", "dim") if more else "",
            )  # fmt: skip
            ev.append(([*head, tail], (len(head), key)))
        elif name == "verify" and not (s["passed"]["packages"] or s["passed"]["submodules"]):
            err = D.clean(s["rejected"][0]["error"]) if s["rejected"] else ""
            lines = [res((f"{t['gloss']['unconfirmed']} x{len(s['statuses'])}: ", "bad"), ", ".join(s["statuses"]))]
            if err:
                lines.append(Line("    ", (err, "bad")))
            if t["overlap"]:
                lines.append(Line("    ", (t["overlap"], "dim")))
            ev.append(([*head, *lines], None))
        elif name == "verify":
            groups: dict[str, list[str]] = {}
            for item, code in s["statuses"].items():
                groups.setdefault(code, []).append(item)
            lines = [res((f"{t['gloss'][code]}: ", "ok"), ", ".join(items)) for code, items in groups.items()]
            em = f"{t['entry_matched']}{str(s['entry_matched']).lower()}"
            lines.append(Line("    ", (em, "ok"), "  ", (t["matched_note"].format(out=s["stdout_head"].strip()), "dim")))
            ev.append(([*head, *lines], (len(head) + len(lines) - 1, em)))
        elif name == "generate_submodule":
            el = s["eliminated_packages"]
            a_, b_ = f"{s['original_size_bytes'] / 1e6:.1f} MB", f"{s['stub_size_bytes'] / 1e6:.1f} MB"
            line = res(
                (t["files"].format(n=s["stub_files"]), "num"), "  ", (t["of"].format(a=a_, b=b_), "dim"), "  ",
                (t["excluded"].format(first=", ".join(el[:2]), more=len(el) - 2), "dim"),
            )  # fmt: skip
            ev.append(([*head, line], None))
        elif name == "generate":
            a_, b_ = f"{s['original_file_count']} files", f"{s['stub_total_bytes'] / 1e6:.1f} MB"
            ev.append(([*head, res((t["files"].format(n=s["stub_file_count"]), "num"), "  ", (t["of"].format(a=a_, b=b_), "dim"))], None))
    return ev


def page_log(lines: list[Line], caption: str, box: tuple[int, str] | None = None) -> State:
    vis = lines[-ROWS:]
    off = len(lines) - len(vis)
    boxes = []
    if box and box[0] >= off:
        boxes = [(box[0] - off, *col_of(vis[box[0] - off], box[1]))]
    return State(vis, caption=caption, boxes=boxes, fit_cols=100)


def shell(text: str, n: int | None = None, cursor: bool = True) -> Line:
    shown = text if n is None else text[:n]
    first, _, rest = shown.partition(" ")
    segs: list[Seg] = [("$ ", "dim"), (first, "cmd"), (" " + rest if rest else "", "")]
    if cursor:
        segs.append((" ", "cur"))
    return Line(*segs)


def build(lang: str, D: Data) -> list[tuple[State, int, str]]:
    """(コマの状態, 何コマ続けるか, 代表のコマの名前) の並び。"""
    t = TEXT[lang]
    seq: list[tuple[State, int, str]] = []
    b = D.builds
    need(b["base"]["stdout"] == b["after"]["stdout"], "ビルド後の exe の出力が基準と違う")
    base_mb, after_mb = mb(b["base"]["bytes"]), mb(b["after"]["bytes"])
    out = b["after"]["stdout"].strip()

    # 1. 登録(このコマンドは実行していないので、出力の行は出さない)
    cmd = "claude mcp add pystub -- uvx mcp-pystub"
    for n in range(0, len(cmd), 2):
        seq.append((State([shell(cmd, n)]), 1, ""))
    seq.append((State([shell(cmd, cursor=False)], caption=t["cap_register"]), 25, "1_register"))

    # 2. 頼む
    head = Line((t["header"], "dim"))
    prompt = t["prompt"]
    step = 3 if lang == "ja" else 4
    for n in range(0, len(prompt), step):
        seq.append((State([head, Line(), Line(("> ", "dim"), prompt[:n], (" ", "cur"))], caption=t["cap_ask"]), 1, ""))
    ask = [head, Line(), Line(("> ", "dim"), prompt)]
    seq.append((State(ask, caption=t["cap_ask"]), 20, "2_ask"))

    # 3. Claude の作業(記録の時間順)
    lines = [*ask, Line()]
    cap = t["cap_claude"]
    events = events_of(D, t)
    need(len(events) >= 5, "場面 3 に出せる出来事が少なすぎる")
    for k, (new, box) in enumerate(events):
        split = next((i for i, ln in enumerate(new) if ln.plain.startswith("  └")), None)
        if split is None:
            lines += new
            seq.append((page_log(lines, cap), 12 if len(new) > 1 else 8, "3_claude_last" if k == len(events) - 1 else ""))
        else:
            lines += new[:split]
            seq.append((page_log(lines, cap), 5, ""))
            lines += new[split:]
            bx = (len(lines) - len(new) + box[0], box[1]) if box else None
            seq.append((page_log(lines, cap, bx), 16 if box else 9, f"3_claude_{k}" if box else ""))
        lines.append(Line())

    # 4. 結果: Claude 自身がビルドした exe の大きさ(基準 → 適用後)と、動かして出た出力
    def bars(frac: float, caption: str = "") -> State:
        n1 = max(1, round(BAR_COLS * b["after"]["bytes"] / b["base"]["bytes"] * frac)) if frac > 0 else 0
        rows = [
            Line((t["build_head"], "dim")), Line(), Line((t["exe_label"], "key")),
            Line("  ", (t["before"], "dim"), "  ", ("█" * BAR_COLS, "bar0"), "  ", (f"{base_mb} MB", "bad")),
            Line("  ", (t["after"], "dim"), "  ", ("█" * n1, "bar1"), "  ", (f"{after_mb} MB" if frac >= 1 else "", "ok")),
            Line(), Line((t["output"], "key")),
            Line("  ", (t["before"], "dim"), "  ", (out, "str")),
            Line("  ", (t["after"], "dim"), "  ", (out, "str"), "   ", (t["match"], "dim"), ("true", "ok")),
        ]  # fmt: skip
        return State(rows, fit_cols=BAR_COLS + 28, zoom=1.0, caption=caption)

    seq.append((bars(0.0), 8, ""))
    for k in range(1, 9):
        seq.append((bars(k / 8), 1, ""))
    seq.append((bars(1.0, t["cap_build"].format(base=base_mb, after=after_mb)), 40, "4_build"))

    # 5. 安全網: 以前に記録した、スタブを当てた状態で Signal.plot()
    p = D.safety["result"]
    run = p["run"]
    need(run["rc"] == 70 and run["stdout"] == "", "止まるスタブの実行が想定と違う")
    need(p["verify"]["rc"] == 0 and p["restore"]["rc"] == 0 and p["bak_exists"] is False and p["pristine"] is True, "復元が確認できていない")
    pe = [ln for ln in D.plot_entry if "Signal(" in ln or ln.strip().startswith("s.plot")]
    need(len(pe) == 2, "plot_entry.py の想定の行が無い")
    msg = wrap(D.clean(run["stderr"].strip()), WRAP_COLS)
    rows5 = [
        Line((t["plot_head"], "dim")),
        Line(("# plot_entry.py", "dim")),
        *[Line(*colorize(ln, PYTOKEN)) for ln in pe],
        Line(("> ", "dim"), ("python plot_entry.py", "cmd")),
    ]
    m0 = len(rows5)
    rows5 += [Line((ln, "bad")) for ln in msg]
    rows5 += [Line((t["exit_code"], "key"), (str(run["rc"]), "bad"))]
    if lang == "en":
        rows5 += [Line((t["g_stop"].format(rc=run["rc"]), "dim"))]
    seq.append((State(rows5[:m0]), 6, ""))
    ex = m0 + len(msg)
    box = [(ex, *col_of(rows5[ex], f"{t['exit_code']}{run['rc']}"))]
    em5 = State(rows5, caption=t["cap_stop"], dim_except=tuple(range(m0, len(rows5))), boxes=box)
    seq.append((replace(em5, dim_except=None, boxes=[], caption=""), 6, ""))
    seq.append((replace(em5, zoom=1.0), 36, "5_stop"))
    return seq


# ---------------------------------------------------------------- 撮る・GIF にする
def browser() -> str:
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if os.path.exists(p):
            return p
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    sys.exit("Edge か Chrome が見つかりません")


def shoot(exe: str, html_text: str, png: Path, profile: Path) -> None:
    page_path = png.with_suffix(".html")
    page_path.write_text(html_text, encoding="utf-8")
    for attempt in range(3):  # 同じ profile を同時に使うと撮れないことがあるので、profile を替えて撮り直す
        subprocess.run(
            [
                exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                f"--user-data-dir={profile}{attempt}", f"--window-size={W},{H}", f"--screenshot={png}", page_path.as_uri(),
            ],
            capture_output=True,
            timeout=120,
            check=False,
        )  # fmt: skip
        if png.exists():
            break
    if not png.exists():
        raise RuntimeError(f"ブラウザが画像を作りませんでした: {png.name}")


def find_ffmpeg(given: str | None) -> str:
    exe = given or shutil.which("ffmpeg")
    if not exe:
        sys.exit("ffmpeg が見つかりません(PATH に置くか --ffmpeg で渡す)")
    return exe


def make_gif(lang: str, D: Data, out: Path, ffmpeg: str, work: Path, key_dir: Path | None, html_dir: Path | None) -> int:
    from PIL import Image, ImageDraw

    seq = build(lang, D)
    exe = browser()
    unique: dict[str, int] = {}
    order = []
    for st, n, key in seq:
        pg = page(st)
        hit = PERSONAL.search(pg)
        if hit:
            raise SystemExit(f"個人の情報らしい文字列が HTML に残っている: {hit.group()!r}")
        order.append((unique.setdefault(pg, len(unique)), n, key))
    shots = work / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    pages = list(unique)

    def one(i: int) -> None:
        shoot(exe, pages[i], shots / f"s{i:04d}.png", work / f"profile{i % 6}")

    with ThreadPoolExecutor(6) as pool:
        list(pool.map(one, range(len(pages))))
    if html_dir:
        html_dir.mkdir(parents=True, exist_ok=True)
        for p in shots.glob("*.html"):
            shutil.copy(p, html_dir / f"{lang}_{p.name}")
    total = sum(n for _i, n, _k in order)
    frames = work / "frames"
    frames.mkdir(exist_ok=True)
    f = 0
    for idx, n, key in order:
        with Image.open(shots / f"s{idx:04d}.png") as raw:
            im = raw.convert("RGB").crop((0, 0, W, H))
        for j in range(n):
            fr = im.copy()
            d = ImageDraw.Draw(fr)
            d.rectangle((0, H - BAR_PX, W, H), fill=(0x2D, 0x33, 0x3B))
            d.rectangle((0, H - BAR_PX, round(W * (f + 1) / total), H), fill=(0x3B, 0x82, 0xF6))
            fr.save(frames / f"f{f:05d}.png")
            if key and j == 0 and key_dir:
                key_dir.mkdir(parents=True, exist_ok=True)
                fr.save(key_dir / f"{lang}_{key}.png")
            f += 1
    gif = out / ("demo.gif" if lang == "en" else f"demo.{lang}.gif")
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y", "-framerate", str(FPS), "-i", str(frames / "f%05d.png"),
            "-filter_complex",
            "split[a][b];[a]palettegen=max_colors=64:stats_mode=full[p];[b][p]paletteuse=dither=none:diff_mode=rectangle",
            "-loop", "0", str(gif),
        ],
        check=True,
    )  # fmt: skip
    size = gif.stat().st_size
    print(
        f"{'OK  ' if size <= MAX_BYTES else 'FAIL'} {gif} ({total} コマ、{total / FPS:.1f} 秒、{size} バイト = {size / 1024 / 1024:.2f} MB、{len(pages)} 枚撮影)"
    )
    return size


def cmd_build(a: argparse.Namespace) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ffmpeg = find_ffmpeg(a.ffmpeg)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    too_big = []
    for lang in a.lang or ["en", "ja"]:
        with tempfile.TemporaryDirectory() as t:
            kd = Path(a.keep_frames) if a.keep_frames else None
            hd = Path(a.keep_html) if a.keep_html else None
            if make_gif(lang, Data(Path(a.data), lang), out, ffmpeg, Path(t), kd, hd) > MAX_BYTES:
                too_big.append(lang)
    if too_big:
        print("3 MB を超えた:", ", ".join(too_big))
    return 1 if too_big else 0




def main() -> int:
    ap = argparse.ArgumentParser(description="README の冒頭の GIF を作る(英語版と日本語版)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("extract", help="記録した会話(stream-json)から画面に出す値を取り出す")
    c.add_argument("--conv", required=True, help="claude -p --output-format stream-json --verbose の出力(長い結果のファイルも同じフォルダに)")
    c.add_argument("--project", required=True, help="Claude が動いた作業場所(パスを相対にするため)")
    c.add_argument("--out", required=True, help="record_<lang>.json の保存先")
    b = sub.add_parser("build", help="保存した記録から GIF を作る")
    b.add_argument("--data", required=True, help="record_<lang>.json などがあるフォルダ")
    b.add_argument("--out", default=str(ROOT / "docs" / "images"), help="置く場所(既定 docs/images)")
    b.add_argument("--ffmpeg", help="ffmpeg の場所(既定は PATH から探す)")
    b.add_argument("--lang", choices=["en", "ja"], action="append", help="作る言語(既定は両方)")
    b.add_argument("--keep-frames", metavar="DIR", help="場面ごとの代表のコマの PNG を DIR に残す(確かめる用)")
    b.add_argument("--keep-html", metavar="DIR", help="撮った HTML を DIR に残す(個人情報の検索用)")
    a = ap.parse_args()
    return cmd_extract(a) if a.cmd == "extract" else cmd_build(a)


if __name__ == "__main__":
    sys.exit(main())
