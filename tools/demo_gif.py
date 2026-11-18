# ruff: noqa: E501  (HTML と CSS の文字列は 1 行が長い)

"""README の冒頭の GIF(analyze → verify → 結果 → 止まるスタブ)を作る(英語版 demo.gif と日本語版 demo.ja.gif)。

使い方(リポジトリの一番上で。ffmpeg が PATH に要る。--ffmpeg でも渡せる):
    # 1. 本物の出力を取って保存する(重い。analyze・verify・スタブを当てた実行。1 回だけ)
    uv run --with pillow python tools/demo_gif.py collect --scenario C:/tmp/pystub-e2e/12_asammdf \
        --builds C:/tmp/pystub-e2e/check_final2/build12_conf/12_asammdf/result.json --data C:/tmp/pystub-e2e/demo_gif
    # 2. 保存した出力から GIF を作る(撮り直しても解析は回さない)
    uv run --with pillow python tools/demo_gif.py build --data C:/tmp/pystub-e2e/demo_gif
    uv run --with pillow python tools/demo_gif.py build --data DIR --out DIR --keep-frames DIR --lang ja

collect が保存するもの(--data の下): analyze.json・verify.json・plot.json(generate_submodule でスタブを作って venv に当て、
Signal.plot() を呼ぶ別のエントリー plot_entry.py を動かし、必ず復元して RECORD 照合とバックアップ領域の残りを確かめた結果)・
builds.json(実ビルドの result.json から exe の大きさと出力だけ抜いたもの)。
Signal.plot() は元の環境では絶対に動かさない(Qt の窓が開く)。スタブを当てた間だけ動かし、QT_QPA_PLATFORM=offscreen も付ける。

build は、保存した出力から場面(前提・analyze・verify・結果・安全網)ごとにコマの状態(打ち込み・字幕・強調・拡大)を作り、
HTML にして Edge か Chrome の headless で撮り、下に進み具合のバーを足して、ffmpeg の palettegen / paletteuse で GIF にする
(880x496、10 fps、3 MB 以下を確かめる)。字幕の数字(サイズ・件数)は保存した出力から読む(手で書かない)。
画面のパスは、プロジェクトからの相対(proj/entry.py、venv/Lib/site-packages)に置き換えて表示する。
値(サイズ・状態・メッセージ)そのものは変えない。個人の情報(ドライブ付きのパス・ユーザー名)が HTML に残っていたら止まる。
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
        "cap_prereq": "Only reads and writes an MDF file, yet the exe is <em>{base} MB</em>",
        "cap_analyze": "The unused GUI part drags in all of <em>PySide6 (Qt)</em>",
        "cap_verify": "Plugs in the stub, <em>actually runs it</em>, and checks the output is the same",
        "cap_result": "exe <em>{base} MB → {after} MB</em>, same output",
        "cap_stop": "Touch what was removed and it does not break silently: it <em>stops and says why</em>",
        "exe_label": "PyInstaller exe (onefile): ",
        "size_note": "(installed size, not exe savings)",
        "g_cond": "(do not import or use anything under asammdf.gui)",
        "g_more": "{n} more",
        "g_confirmed": "(confirmed by running)",
        "g_match": "(stdout bytes and exit code are the same as without the stub)",
        "g_stop": "(English gloss: stub of asammdf.gui; Signal.plot() was called, so it stopped with exit code {rc})",
        "before": "before",
        "after": "after ",
        "output": "output",
        "build_head": "# build with PyInstaller, then run the exe",
        "plot_head": "# run an entry that calls Signal.plot() in the venv with the stub applied",
        "exit_code": "exit code: ",
        "applied": "applied: asammdf.gui (confirmed by running)",
        "match": "match: ",
    },
    "ja": {
        "cap_prereq": "MDF を読み書きするだけなのに <em>{base} MB</em>",
        "cap_analyze": "使っていない GUI の部分が <em>PySide6(Qt)</em>を丸ごと引き込んでいる",
        "cap_verify": "スタブを差し込んで<em>実際に動かし</em>、出力が同じことを確かめる",
        "cap_result": "exe <em>{base} MB → {after} MB</em>、出力は同じ",
        "cap_stop": "外した部分に触れたら、黙って壊れず、<em>理由を出して止まる</em>",
        "exe_label": "PyInstaller の exe(onefile): ",
        "size_note": "(インストール済みの大きさ)",
        "g_cond": "",
        "g_more": "ほか {n} 件",
        "g_confirmed": "",
        "g_match": "(標準出力のバイト列と終了コードがスタブ無しと同じ)",
        "g_stop": "",
        "before": "適用前",
        "after": "適用後",
        "output": "出力",
        "build_head": "# PyInstaller でビルドして exe を動かす",
        "plot_head": "# スタブを当てた venv で、Signal.plot() を呼ぶエントリーを動かす",
        "exit_code": "終了コード: ",
        "applied": "適用: asammdf.gui(確認済み(実行))",
        "match": "一致: ",
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


# ---------------------------------------------------------------- 本物の出力
PERSONAL = re.compile(r"[A-Za-z]:[\\/]|[\\/]Users[\\/]|hunte|AppData", re.I)


class Data:
    """collect が保存した出力を読み、画面に出す値を取り出す(置き換えは表示だけ)。"""

    def __init__(self, d: Path):
        def load(name: str):
            return json.loads((d / name).read_text(encoding="utf-8"))

        self.analyze = load("analyze.json")["result"]
        self.verify = load("verify.json")["result"]
        self.plot = load("plot.json")
        self.builds = load("builds.json")
        self.entry = (d / "entry.py").read_text(encoding="utf-8").splitlines()
        self.plot_entry = (d / "plot_entry.py").read_text(encoding="utf-8").splitlines()
        self.scenario = str(Path(self.analyze["project_root"]).parent)

    def clean(self, s: str) -> str:
        """シナリオのフォルダを取り除く(プロジェクトからの相対にする)。"""
        for r in {self.scenario, self.scenario.replace("\\", "/")}:
            s = s.replace(r + "\\", "").replace(r + "/", "")
        return s


def need(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"保存した出力が想定と違う: {msg}")


def facts(D: Data) -> dict:
    """画面に出す値。すべて保存した出力から読む。"""
    a, v, p, b = D.analyze, D.verify, D.plot["result"], D.builds
    hint = next(h for h in a["conditional_submodule_hints"] if h["target_package"] == "PySide6")
    rec = a["summary"]["recommendations"][0]
    m = re.search(r"ほか (\d+) 件", rec)
    need(bool(m), "推奨の先頭に「ほか N 件」が無い")
    need(
        rec.startswith("[high]") and hint["submodule"] in rec and f"{hint['estimated_savings_mb']}" in rec, "推奨の先頭と hint が合わない"
    )
    conds = [c["entry"] for c in hint["conditions"][:3]]
    need(len(conds) == 3, "条件が 3 件未満")
    st = v["statuses"][hint["submodule"]]
    base, conf = b["base"], b["proven_plus_conditional"]
    out = v["entry_conditions"]["stdout_head"].strip()
    need(conf["match"] is True and conf["stdout"] == base["stdout"] == out, "出力が一致していない")
    need(v["entry_matched"] is True and st["status"] == "confirmed_run", "verify が確認済み(実行)でない")
    run = p["run"]
    need(run["rc"] == 70 and run["stdout"] == "", "止まるスタブの実行が想定と違う")
    need(
        p["verify"]["rc"] == 0 and p["restore"]["rc"] == 0 and p["bak_exists"] is False and p["pristine"] is True, "復元が確認できていない"
    )
    return dict(
        sub=hint["submodule"], target=hint["target_package"], conf=hint["confidence"], mb=hint["estimated_savings_mb"],
        conds=conds, more=int(m.group(1)), uses=hint["conditions"][0]["project_uses_count"],
        status_key=next(k for k, items in v["status_summary"].items() if hint["submodule"] in items),
        targets=st["observed"]["targets"], matched=v["entry_matched"], out=out,
        base_mb=f"{base['exe_mb']:.1f}", after_mb=f"{conf['exe_mb']:.1f}", base_f=base["exe_mb"], after_f=conf["exe_mb"],
        stderr=run["stderr"].strip(), rc=run["rc"],
    )  # fmt: skip


# ---------------------------------------------------------------- 場面
def build(lang: str, D: Data) -> list[tuple[State, int, str]]:
    """(コマの状態, 何コマ続けるか, 代表のコマの名前) の並び。"""
    t, f = TEXT[lang], facts(D)
    gloss = lang == "en"
    seq: list[tuple[State, int, str]] = []
    entry_rel = "proj/entry.py"
    sp_rel = "venv/Lib/site-packages"

    def typing(parts: list[str]) -> None:
        total = len("\n".join(parts))
        for n in range(0, total, 5):
            seq.append((State(call_lines(parts, n, True)), 1, ""))
        seq.append((State(call_lines(parts, None, True)), 3, ""))

    def zoom_in(em: State, key: str, hold: int) -> None:
        for k in range(1, 5):
            seq.append((replace(em, zoom=k / 4, dim_except=None, boxes=[], caption=""), 1, ""))
        seq.append((replace(em, zoom=1.0), hold, key))

    # 1. 前提: entry.py と exe の大きさ
    code = [Line(("# " + entry_rel, "dim"))] + [Line(*colorize(ln, PYTOKEN)) for ln in D.entry]
    exe = Line(t["exe_label"], (f"{f['base_mb']} MB", "bad"))
    s1 = State([*code, Line(), exe], zoom=1.0, fit_cols=66, max_zoom=1.2)
    seq.append((replace(s1, lines=[*code]), 16, ""))
    row = len(code) + 1
    box1 = (row, *col_of(exe, f"{f['base_mb']} MB"))
    seq.append((replace(s1, caption=t["cap_prereq"].format(base=f["base_mb"]), dim_except=(row,), boxes=[box1]), 30, "1_prereq"))

    # 2. analyze
    args = [f'analyze(entry_point="{entry_rel}", python_path="{sp_rel}")']
    typing(args)
    rows2 = [
        Line(("conditional_submodule_hints[0]", "dim")),
        Line(("  submodule: ", "key"), (f["sub"], "str")),
        Line(("  target_package: ", "key"), (f["target"], "str")),
        Line(("  confidence: ", "key"), (f["conf"], "ok")),
        Line(("  estimated_savings_mb: ", "key"), (str(f["mb"]), "num"), ("  " + t["size_note"], "dim")),
        Line(("  conditions:", "key")),
        Line("    - ", (D.clean(f["conds"][0]), "str")),
        Line("    - ", (D.clean(f["conds"][1]), "str")),
        Line("    - ", (D.clean(f["conds"][2]), "str")),
        Line("    - ", (t["g_more"].format(n=f["more"]), "dim")),
        Line(("  project_uses_count: ", "key"), (str(f["uses"]), "num")),
    ]
    if gloss:
        rows2.insert(7, Line(("      " + t["g_cond"], "dim")))
    r0 = 2
    base2 = [*call_lines(args), Line(), *rows2]
    seq.append((State(base2), 6, ""))
    i_sub, i_tgt, i_conf, i_mb = r0 + 1, r0 + 2, r0 + 3, r0 + 4
    i_c0, i_c1 = r0 + 6, r0 + 6 + (2 if gloss else 1)
    boxes2 = [
        (i_sub, *col_of(base2[i_sub], f["sub"])),
        (i_tgt, *col_of(base2[i_tgt], f["target"])),
        (i_conf, *col_of(base2[i_conf], f["conf"])),
        (i_mb, *col_of(base2[i_mb], str(f["mb"]))),
        (i_c0, *col_of(base2[i_c0], D.clean(f["conds"][0]))),
        (i_c1, *col_of(base2[i_c1], D.clean(f["conds"][1]))),
    ]
    em2 = State(base2, caption=t["cap_analyze"], dim_except=(r0, i_sub, i_tgt, i_conf, i_mb, i_c0 - 1, *range(i_c0, i_c1 + 1)), boxes=boxes2)
    zoom_in(em2, "2_analyze", 34)

    # 3. verify
    args = [
        f'verify(entry_point="{entry_rel}", python_path="{sp_rel}",',
        f'       submodules=["{f["sub"]}=>{f["target"]}"], run_entry=True, record=True)',
    ]
    typing(args)
    rows3 = [
        Line(("status_summary", "dim")),
        Line("  ", (f["status_key"], "ok"), ": ", (f'["{f["sub"]}"]', "str"), ("  " + t["g_confirmed"], "dim") if gloss else ""),
        Line(("statuses[", "dim"), (f'"{f["sub"]}"', "str"), ("].observed.targets", "dim")),
        Line("  ", (f["target"], "str"), ": ", (f["targets"][f["target"]], "ok")),
        Line(("entry_matched: ", "key"), (str(f["matched"]).lower(), "ok")),
        Line(("entry_conditions.stdout_head: ", "key"), (f'"{f["out"]}"', "str")),
        Line("  ", (t["g_match"], "dim")),
    ]
    base3 = [*call_lines(args), Line(), *rows3]
    r0 = len(base3) - len(rows3)
    seq.append((State(base3), 6, ""))
    boxes3 = [
        (r0 + 1, *col_of(base3[r0 + 1], f["status_key"])),
        (r0 + 3, *col_of(base3[r0 + 3], f"{f['target']}: {f['targets'][f['target']]}")),
        (r0 + 4, *col_of(base3[r0 + 4], f"entry_matched: {str(f['matched']).lower()}")),
    ]
    em3 = State(base3, caption=t["cap_verify"], dim_except=(r0, r0 + 1, r0 + 2, r0 + 3, r0 + 4), boxes=boxes3)
    zoom_in(em3, "3_verify", 34)

    # 4. 結果
    def bars(frac: float) -> State:
        n0 = BAR_COLS
        n1 = round(BAR_COLS * f["after_f"] / f["base_f"] * frac)
        ok = D.builds["proven_plus_conditional"]["match"]
        rows = [
            Line((t["build_head"], "dim")),
            Line(),
            Line(("exe", "key")),
            Line("  ", (t["before"], "dim"), "  ", ("█" * n0, "bar0"), "  ", (f"{f['base_mb']} MB", "bad")),
            Line("  ", (t["after"], "dim"), "  ", ("█" * n1, "bar1"), "  ", (f"{f['after_mb']} MB" if frac >= 1 else "", "ok")),
            Line(),
            Line((t["output"], "key")),
            Line("  ", (t["before"], "dim"), "  ", (f["out"], "str")),
            Line("  ", (t["after"], "dim"), "  ", (f["out"], "str"), "   ", (t["match"], "dim"), (str(ok).lower(), "ok")),
            Line(),
            Line((t["applied"], "dim")),
        ]
        return State(rows, fit_cols=BAR_COLS + 28, zoom=1.0)

    seq.append((bars(0.0), 6, ""))
    for k in range(1, 9):
        seq.append((bars(k / 8), 1, ""))
    seq.append((replace(bars(1.0), caption=t["cap_result"].format(base=f["base_mb"], after=f["after_mb"])), 30, "4_result"))

    # 5. 安全網: スタブを当てた状態で Signal.plot()
    pe = [ln for ln in D.plot_entry if "Signal(" in ln or ln.strip().startswith("s.plot")]
    need(len(pe) == 2, "plot_entry.py の想定の行が無い")
    msg = wrap(D.clean(f["stderr"]), WRAP_COLS)
    rows5 = [
        Line((t["plot_head"], "dim")),
        Line(("# plot_entry.py", "dim")),
        *[Line(*colorize(ln, PYTOKEN)) for ln in pe],
        Line(("> ", "dim"), ("python plot_entry.py", "cmd")),
    ]
    m0 = len(rows5)
    rows5 += [Line((ln, "bad")) for ln in msg]
    rows5 += [Line((t["exit_code"], "key"), (str(f["rc"]), "bad"))]
    if gloss:
        rows5 += [Line((t["g_stop"].format(rc=f["rc"]), "dim"))]
    seq.append((State(rows5[:m0]), 8, ""))
    ex = m0 + len(msg)
    em5 = State(rows5, caption=t["cap_stop"], dim_except=tuple(range(m0, len(rows5))), boxes=[(ex, *col_of(rows5[ex], f"{t['exit_code']}{f['rc']}"))])
    seq.append((replace(em5, dim_except=None, boxes=[], caption=""), 8, ""))
    seq.append((replace(em5, zoom=1.0), 40, "5_stop"))
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
    D = Data(Path(a.data))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    too_big = []
    for lang in a.lang or ["en", "ja"]:
        with tempfile.TemporaryDirectory() as t:
            kd = Path(a.keep_frames) if a.keep_frames else None
            hd = Path(a.keep_html) if a.keep_html else None
            if make_gif(lang, D, out, ffmpeg, Path(t), kd, hd) > MAX_BYTES:
                too_big.append(lang)
    if too_big:
        print("3 MB を超えた:", ", ".join(too_big))
    return 1 if too_big else 0


# ---------------------------------------------------------------- 本物の出力を取る
def free_mb() -> float:
    if os.name != "nt":
        return 1e9
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
        capture_output=True, encoding="utf-8", check=False,
    )  # fmt: skip
    return float(r.stdout.strip()) / 1024


def wait_memory(min_gb: float = 4.0) -> None:
    while free_mb() < min_gb * 1024:
        print(f"空きメモリが {min_gb} GB 未満。60 秒待つ")
        time.sleep(60)


def tree_hash(root: Path) -> dict[str, str]:
    import hashlib

    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def cmd_collect(a: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    from mcp_pystub.server import analyze, generate_submodule, verify

    S, data = Path(a.scenario), Path(a.data)
    data.mkdir(parents=True, exist_ok=True)
    sp = S / "venv" / "Lib" / "site-packages"
    bak = S / "venv" / "Lib" / "_pystub_bak"
    py = S / "venv" / "Scripts" / "python.exe"
    entry = S / "proj" / "entry.py"
    need(not bak.exists(), "_pystub_bak が残っている(先に復元する)")

    def save(name: str, call: dict, result: dict) -> None:
        (data / name).write_text(json.dumps({"call": call, "result": result}, ensure_ascii=False, indent=1), encoding="utf-8")

    shutil.copy(entry, data / "entry.py")
    wait_memory()
    ca = dict(entry_point=str(entry), python_path=str(sp))
    save("analyze.json", ca, analyze(**ca))
    wait_memory()
    cv = dict(**ca, packages=[], submodules=["asammdf.gui=>PySide6"], nofollow=[], run_entry=True, record=True)
    save("verify.json", cv, verify(**cv))

    # builds.json: 実ビルドの result.json から、exe の大きさと出力だけ
    rj = json.loads(Path(a.builds).read_text(encoding="utf-8"))["variants"]
    keep = ("exe_bytes", "exe_mb", "match", "stdout", "run_rc")
    names = ("base", "proven_plus_conditional")
    (data / "builds.json").write_text(
        json.dumps({k: {x: rj[k][x] for x in keep} for k in names}, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 止まるスタブ: 元の環境では Signal.plot() を動かさない。スタブを当てた間だけ動かし、必ず復元する
    plot_entry = data / "plot_entry.py"
    plot_entry.write_text(
        "import numpy as np\nfrom asammdf import Signal\nt = np.arange(0, 10, 1.0)\n"
        's = Signal(samples=t*2, timestamps=t, name="sig1", unit="V")\ns.plot()\nprint("plotted")\n',
        encoding="utf-8",
    )
    bash = shutil.which("bash")
    need(bool(bash), "bash が無い(build_instructions の手順は bash)")
    pre = tree_hash(sp / "asammdf")
    cg = dict(entry_point=str(entry), parent_package="asammdf", submodule="asammdf.gui", python_path=str(sp))
    g = generate_submodule(**cg)
    for rel, txt in g["files"].items():
        p = data / "_stubs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt, encoding="utf-8")
    bi = g["build_instructions"]
    env = dict(os.environ, PATH=str(py.parent) + os.pathsep + os.environ["PATH"], PYTHONUTF8="1")

    def sh(cmds: list[str]) -> dict:
        r = subprocess.run(
            [bash, "-c", "\n".join(cmds)], cwd=data, env=env, capture_output=True, encoding="utf-8", errors="replace", check=False
        )
        return {"rc": r.returncode, "out": r.stdout + r.stderr}

    res: dict = {"stub_size_bytes": g["stub_size_bytes"], "eliminated_packages": g["eliminated_packages"]}
    try:
        res["backup"], res["install"] = sh(bi["backup_commands"]), sh(bi["install_commands"])
        init = sp / "asammdf" / "gui" / "__init__.py"
        need(
            res["backup"]["rc"] == 0 and res["install"]["rc"] == 0 and init.stat().st_size < 10000,
            "スタブが当たっていない(plot は動かさない)",
        )
        run_env = dict(env, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        p = subprocess.run(
            [str(py), str(plot_entry)], cwd=data, env=run_env, capture_output=True, encoding="utf-8", errors="replace", timeout=60, check=False
        )
        res["run"] = {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    finally:
        un = bi["uninstall_commands"]
        res["restore"] = sh(un[: un.index("")])  # 方法 1(バックアップから復元)だけ。方法 2 は pip install なので動かさない
        res["verify"] = sh(bi["verify_commands"])  # 対象の python で __file__・RECORD のハッシュ・版を確かめる
    res["bak_exists"] = bak.exists()
    res["pristine"] = tree_hash(sp / "asammdf") == pre
    save("plot.json", cg, res)
    print(json.dumps({k: res[k] for k in ("restore", "verify", "bak_exists", "pristine")}, ensure_ascii=False, indent=1))
    print(json.dumps(res.get("run"), ensure_ascii=False))
    return 0 if res["pristine"] and not res["bak_exists"] and res["verify"]["rc"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="README の冒頭の GIF を作る(英語版と日本語版)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="本物の出力を取って保存する(重い)")
    c.add_argument("--scenario", required=True, help="シナリオのフォルダ(proj/entry.py と venv がある)")
    c.add_argument("--builds", required=True, help="実ビルドの result.json(base と proven_plus_conditional が入っているもの)")
    c.add_argument("--data", required=True, help="保存先")
    b = sub.add_parser("build", help="保存した出力から GIF を作る")
    b.add_argument("--data", required=True, help="collect の保存先")
    b.add_argument("--out", default=str(ROOT / "docs" / "images"), help="置く場所(既定 docs/images)")
    b.add_argument("--ffmpeg", help="ffmpeg の場所(既定は PATH から探す)")
    b.add_argument("--lang", choices=["en", "ja"], action="append", help="作る言語(既定は両方)")
    b.add_argument("--keep-frames", metavar="DIR", help="場面ごとの代表のコマの PNG を DIR に残す(確かめる用)")
    b.add_argument("--keep-html", metavar="DIR", help="撮った HTML を DIR に残す(個人情報の検索用)")
    a = ap.parse_args()
    return cmd_collect(a) if a.cmd == "collect" else cmd_build(a)


if __name__ == "__main__":
    sys.exit(main())
