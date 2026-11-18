# ruff: noqa: E501  (HTML と CSS の文字列は 1 行が長い)

"""README の冒頭の GIF(結果 → 使い方 → Claude がやること(exe の中身の箱) → 結果)を作る(英語版 demo.gif と日本語版 demo.ja.gif)。

GIF は、本物の Claude Code の会話の記録から描く。作り話の画面は使わない。

使い方(リポジトリの一番上で。ffmpeg が PATH に要る。--ffmpeg でも渡せる):
    # 1. 記録した会話(claude -p --output-format stream-json --verbose)から、画面に出す値を取り出す(言語ごと)
    uv run python tools/demo_gif.py extract --conv DIR/ja/conv.jsonl --project C:/work/demo/ja --out DIR/record_ja.json
    # 2. 保存した記録から GIF を作る(DIR に record_ja.json・record_en.json・builds_ja.json・builds_en.json・contents_en.json・safety/ がある)
    uv run --with pillow python tools/demo_gif.py build --data DIR
    uv run --with pillow python tools/demo_gif.py build --data DIR --out DIR --keep-frames DIR --keep-html DIR --lang ja

DIR に置くもの:
  record_<lang>.json  extract が作る。ツールの呼び出しと結果の要点・Claude の返事・所要時間・費用。
  builds_<lang>.json  Claude 自身がビルドした exe の大きさと、その exe を Claude が動かして出た出力(記録の Bash の出力から)
                      {"base": {"bytes", "stdout"}, "after": {"bytes", "stdout"}}。
  contents_<lang>.json  ビルド前後の exe の中身の内訳。PyInstaller の CArchiveReader で exe の中を数え、圧縮後の大きさを
                      最上位の名前ごとに足した値 {"before": {"total", "PySide6"}, "after": {"total", "PySide6"}}(合計は builds と一致を確かめる)。
  safety/plot.json・safety/plot_entry.py  スタブを当てた venv を必ず復元できたことの記録(GIF には出さないが、復元の確認に使う)。

build は、4 つの場面(結果のグラフ → 使い方(① 登録と ② 頼む) → Claude がやること → 結果と登録コマンド)を、
大きな文字の HTML にして Edge か Chrome の headless で撮り、下に進み具合のバーを足して、ffmpeg の palettegen / paletteuse で
GIF にする(880x496、10 fps、約 25 秒、3 MB 以下を確かめる)。ツール呼び出しの引数は流さず、何をしたかと結果の一言だけを出す。
数字(サイズ・外す部分・排除される物の大きさ・出力)は記録から読む(手で書かない)。
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W, H = 880, 496  # GIF の大きさ(px)
FPS = 10
BAR_PX = 6  # 進み具合のバー
MAX_BYTES = 3 * 1024 * 1024

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

        self.rec = load(f"record_{lang}.json")  # 数字は英語の会話の記録から(言語を変えても同じ数字)
        self.builds = load(f"builds_{lang}.json")
        self.safety = load("safety/plot.json")
        self.contents = load(f"contents_{lang}.json")  # exe の中身の内訳(PyInstaller の CArchiveReader で数えた値)
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


# ---------------------------------------------------------------- 場面(説明する流れの 7 場面。画面は大きな文字の HTML)
TEXT = {
    "en": {
        "reg_cap": "① Register (once)",
        "ask_cap": "② Ask Claude Code",
        "prompt": "Use mcp-pystub to make the exe of main.py smaller.",
        "howto": "When your PyInstaller exe is too big",
        "howto_cap": "",
        "chart_cap": "Example: an app that uses asammdf",
        "chart_title": "PyInstaller exe size",
        "lbl0": "As is",
        "lbl1": "Without {target} (mcp-pystub)",
        "check": "Works the same",
        "todo": "What Claude does",
        "c_exe": "Inside main.exe",
        "c_other": "numpy, pandas, Python, ...",
        "c_unused": "main.py never uses it",
        "c_stub": "stub",
        "c_same": "✓ Run it: same result",
        "c0": "① Look inside the exe",
        "c1": "② {target} (Qt, used only by the asammdf GUI) takes {big} MB",
        "c2": "③ Replace the unused part with a stub (an empty stand-in)",
        "c3": "④ Same result, {base} MB → {after} MB",
        "mb": "MB",
    },
    "ja": {
        "reg_cap": "① 登録(1 回だけ)",
        "ask_cap": "② Claude Code に頼む",
        "prompt": "mcp-pystub で main.py の exe を小さくして",
        "howto": "PyInstaller の exe が大きすぎるとき",
        "howto_cap": "",
        "chart_cap": "例: asammdf を使うアプリ",
        "chart_title": "PyInstaller の exe の大きさ",
        "lbl0": "そのまま",
        "lbl1": "{target} を外した後 (mcp-pystub)",
        "check": "動作は同じ",
        "todo": "Claude がやること",
        "c_exe": "main.exe の中身",
        "c_other": "numpy・pandas・Python など",
        "c_unused": "main.py は使っていない",
        "c_stub": "スタブ",
        "c_same": "✓ 動かして結果は同じ",
        "c0": "① exe の中身を調べる",
        "c1": "② {target}(Qt、asammdf の GUI 用)が {big} MB",
        "c2": "③ 使っていない部分をスタブ(空の代わり)に置き換える",
        "c3": "④ 結果は同じまま、{base} MB → {after} MB",
        "mb": "MB",
    },
}

BASE_CSS = f"""*{{box-sizing:border-box}}
html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#1e1e1e;color:#d4d4d4;
font-family:"Segoe UI","Yu Gothic UI","Meiryo",sans-serif}}
.mono{{font-family:"Cascadia Mono",Consolas,"BIZ UDGothic","MS Gothic",monospace}}
.dim{{color:#808080}} .ok{{color:#23d18b}} .bad{{color:#f14c4c}} .cyan{{color:#9cdcfe}} .str{{color:#ce9178}}
.cap{{position:absolute;left:50%;bottom:{BAR_PX + 20}px;transform:translateX(-50%);max-width:{W - 40}px;width:max-content;text-align:center;
padding:9px 24px;border-radius:26px;background:#f6f8fa;color:#1f2328;font-weight:700;font-size:21px;line-height:1.3;
box-shadow:0 2px 14px rgba(0,0,0,.45)}}
"""


def doc(body: str, caption: str = "", css: str = "") -> str:
    cap = f'<div class="cap">{caption}</div>' if caption else ""
    return f'<!doctype html><meta charset="utf-8"><style>{BASE_CSS}{css}</style><body>{body}{cap}</body>'


CHART_CSS = """
.chart{position:absolute;left:44px;top:26px;width:800px}
.ct{font-size:20px;color:#808080;margin-bottom:10px}
.lbl{font-size:27px;font-weight:700;color:#aab2bd;margin:12px 0 6px}
.row{display:flex;align-items:center;height:62px}
.bar{height:58px;border-radius:7px}
.num{margin-left:18px;font-size:58px;font-weight:800;white-space:nowrap}
.out{margin-top:28px;font-size:26px;font-weight:700;color:#d4d4d4}
.outsub{margin-top:6px;font-size:20px;color:#808080}
.cmd{margin-top:34px;font-size:30px;white-space:nowrap}
"""
BAR_PX_MAX = 500


def chart_page(t: dict, base: float, after: float, frac: float, tail_html: str, caption: str) -> str:
    """frac: 0 = 後の棒がまだ元と同じ長さ、1 = 縮み終わった。数字は棒の長さに合わせて動く。"""
    e = 1 - (1 - frac) ** 3  # ease-out
    val = base + (after - base) * e
    w1 = BAR_PX_MAX * val / base
    col = "#23d18b" if frac >= 1 else "#e0a030" if frac > 0 else "#f14c4c"
    body = (
        f'<div class="chart"><div class="ct">{t["chart_title"]}</div>'
        f'<div class="lbl">{t["lbl0"]}</div><div class="row"><div class="bar" style="width:{BAR_PX_MAX}px;background:#f14c4c"></div>'
        f'<div class="num bad">{base:.1f} {t["mb"]}</div></div>'
        f'<div class="lbl">{t["lbl1"]}</div><div class="row"><div class="bar" style="width:{w1:.1f}px;background:{col}"></div>'
        f'<div class="num" style="color:{col}">{val:.1f} {t["mb"]}</div></div>{tail_html}</div>'
    )
    return doc(body, caption, CHART_CSS)


TYPE_CSS = """
.box{position:absolute;left:40px;top:120px;width:800px;min-height:130px;padding:26px 30px;border-radius:12px;background:#111;border:2px solid #3a3a3a;
overflow-wrap:anywhere}
.cursor{display:inline-block;width:.6em;background:#d4d4d4;color:#1e1e1e}
.hid{visibility:hidden}
.how{position:absolute;left:40px;top:24px;width:800px}
.how h1{font-size:34px;margin:0 0 14px;color:#d4d4d4}
.step{font-size:24px;font-weight:700;color:#aab2bd;margin:16px 0 8px}
.how .box{position:static;width:800px;min-height:0;padding:16px 22px}
"""


def howto_page(t: dict, cmd: str, n_cmd: int, pr: str, n_pr: int, psz: int) -> str:
    """① 登録のコマンドと ② 頼む文を 1 画面に出す。n_cmd・n_pr はそれぞれ打ち終わった文字数。"""
    esc = html.escape

    def line(prefix: str, text: str, n: int, mono: bool, cursor: bool) -> str:
        shown, rest = text[:n], text[n:]
        if mono:
            first, sp, tail = shown.partition(" ")
            b = f'<span class="dim">{esc(prefix)}</span><span class="cyan">{esc(first)}</span>{esc(sp + tail)}'
        else:
            b = f'<span class="dim">{esc(prefix)}</span>{esc(shown)}'
        if cursor:
            b += '<span class="cursor"> </span>'
        return b + f'<span class="hid">{esc(rest)}</span>'

    typing_cmd = n_cmd < len(cmd)
    typing_pr = not typing_cmd and n_pr < len(pr)
    body = (
        f'<div class="how"><h1>{t["howto"]}</h1>'
        f'<div class="step">{t["reg_cap"]}</div>'
        f'<div class="box mono" style="font-size:25px;line-height:1.4">{line("$ ", cmd, n_cmd, True, typing_cmd)}</div>'
        f'<div class="step">{t["ask_cap"]}</div>'
        f'<div class="box" style="font-size:{psz}px;line-height:1.4">{line("> ", pr, n_pr, False, typing_pr)}</div></div>'
    )
    return doc(body, t["howto_cap"], TYPE_CSS)




def build(lang: str, D: Data) -> list[tuple[str, int, str]]:
    """(コマの HTML, 何コマ続けるか, 代表のコマの名前) の並び。全体で約 28 秒。数字は記録から読む。"""
    t = dict(TEXT[lang])
    seq: list[tuple[str, int, str]] = []
    b = D.builds
    need(b["base"]["stdout"] == b["after"]["stdout"], "ビルド後の exe の出力が基準と違う")
    base, after = b["base"]["bytes"] / 1e6, b["after"]["bytes"] / 1e6
    base_s, after_s = mb(b["base"]["bytes"]), mb(b["after"]["bytes"])
    out = b["after"]["stdout"].strip()

    # 記録から読む値: analyze の先頭の推奨(外す部分・排除される物・その大きさ)、確認した出力、ビルドの前後
    names = [s["name"].split("__")[-1] for s in D.steps if s["kind"] == "use"]
    need({"analyze", "verify", "generate_submodule"} <= set(names), "記録に analyze / verify / generate_submodule が無い")
    an = next(s for s in D.steps if s["kind"] == "use" and s["name"].endswith("analyze"))["summary"]
    m = re.match(r"\[\w+\] (\S+) を.*?(\S+) を排除できます（約 ([\d.]+) MB.*?プロジェクト内の使用 0 件", an["recs"][0])
    need(bool(m), "analyze の推奨の先頭が想定の形でない")
    sub, target, size = m.groups()  # type: ignore[union-attr]
    t = {k: v.replace("{target}", f'<span class="str">{target}</span>') for k, v in t.items()}  # 外す物の名前(記録から)
    ver = next(s for s in D.steps if s["kind"] == "use" and s["name"].endswith("verify") and s["summary"]["entry_matched"])["summary"]
    need(ver["stdout_head"].strip() == out, "verify の出力と exe の出力が違う")
    p = D.safety["result"]
    need(p["restore"]["rc"] == 0 and p["bak_exists"] is False and p["pristine"] is True, "復元が確認できていない")
    run = p["run"]
    need(run["rc"] == 70 and run["stdout"] == "", "止まるスタブの実行が想定と違う")

    cap_chart = t["chart_cap"].format(base=base_s, after=after_s)
    out_html = f'<div class="out"><span class="ok">✓</span> {t["check"]}</div>'
    cmd = "claude mcp add pystub -- uvx mcp-pystub"

    # 1. 結果のグラフ(5 秒 = 50 コマ)。後の棒が縮む
    seq.append((chart_page(t, base, after, 0, out_html, cap_chart), 6, "1a_start"))
    for k in range(1, 9):
        seq.append((chart_page(t, base, after, k / 8, out_html, cap_chart), 1, ""))
    seq.append((chart_page(t, base, after, 1, out_html, cap_chart), 36, "1b_end"))
    # 2. 使い方: ① 登録と ② 頼むを 1 画面で、順に打つ(約 7 秒)
    pr = t["prompt"]
    stp = 2 if lang == "ja" else 3
    psz = 27 if lang == "ja" else 24
    nf = 0
    for n in range(0, len(cmd), 2):
        seq.append((howto_page(t, cmd, n, pr, 0, psz), 1, "")); nf += 1
    seq.append((howto_page(t, cmd, len(cmd), pr, 0, psz), 5, "2a_register")); nf += 5
    for n in range(0, len(pr), stp):
        seq.append((howto_page(t, cmd, len(cmd), pr, n, psz), 1, "")); nf += 1
    seq.append((howto_page(t, cmd, len(cmd), pr, len(pr), psz), max(20, 70 - nf), "2b_ask"))
    # 3. Claude がやること: exe の中身の箱で、使っていない target がスタブに縮む(約 11 秒)
    C = D.contents
    need(abs(C["before"]["total"] - b["base"]["bytes"]) < 1 and abs(C["after"]["total"] - b["after"]["bytes"]) < 1, "中身の内訳とビルドの大きさが合わない")
    seq.append((contents_page(t, 0, 0, C, target), 22, "3a_contents"))
    seq.append((contents_page(t, 1, 0, C, target), 26, "3b_unused"))
    for k in range(1, 11):
        seq.append((contents_page(t, 2, k / 10, C, target), 1, ""))
    seq.append((contents_page(t, 2, 1.0, C, target), 18, "3c_stub"))
    seq.append((contents_page(t, 3, 1.0, C, target), 32, "3d_result"))
    # 4. 結果に戻って、登録コマンド(2 秒)
    cmd_html = f'<div class="cmd mono"><span class="dim">$ </span><span class="cyan">claude</span> {html.escape(cmd.split(" ", 1)[1])}</div>'
    seq.append((chart_page(t, base, after, 1, cmd_html, ""), 20, "7_end"))
    return seq



CONT_CSS = """
.h{position:absolute;left:44px;top:20px;font-size:34px;font-weight:800}
.tot{position:absolute;right:44px;top:14px;font-size:52px;font-weight:800;font-variant-numeric:tabular-nums}
.lab{position:absolute;left:44px;top:112px;font-size:19px;color:#9aa3ad;font-weight:600;letter-spacing:.5px}
.exe{position:absolute;left:40px;top:142px;height:132px;border-radius:16px;border:3px solid #586070;background:#16181c;
display:flex;align-items:stretch;padding:6px;gap:6px;overflow:hidden}
.seg{border-radius:10px;display:flex;flex-direction:column;justify-content:center;align-items:center;overflow:hidden;white-space:nowrap}
.other{background:linear-gradient(160deg,#3b82f6,#1d4ed8)}
.qt{background:repeating-linear-gradient(135deg,rgba(255,255,255,.06) 0 12px,rgba(0,0,0,0) 12px 24px),linear-gradient(160deg,#fb923c,#c2410c);
color:#fff;font-weight:800}
.qt .nm{font-size:40px;text-shadow:0 2px 6px rgba(0,0,0,.35)} .qt .sz{font-size:22px;opacity:.9}
.qt.glow{box-shadow:0 0 0 4px #f14c4c inset}
.stub{border:3px dashed #9cdcfe;background:#0f2533;color:#9cdcfe;font-size:15px;font-weight:700}
.under{position:absolute;top:286px;font-size:18px;color:#b5bcc6;white-space:nowrap}
.warn{position:absolute;top:104px;font-size:24px;font-weight:800;color:#f14c4c;white-space:nowrap}
.ghost{position:absolute;left:40px;top:142px;width:812px;height:132px;border-radius:16px;border:3px dashed #3a3f48;
display:flex;align-items:center;justify-content:flex-end;padding-right:40px;font-size:44px;font-weight:800;color:#3f4651}
.same{position:absolute;left:44px;top:330px;font-size:30px;font-weight:800;color:#23d18b}
"""
EXE_PX = 800


def contents_page(t: dict, step: int, frac: float, C: dict, target: str) -> str:
    """exe の中身の箱。step 0: 中身 / 1: target が使われていない / 2: target がスタブに縮む(frac 0→1) / 3: 結果。"""
    base, after = C["before"]["total"] / 1e6, C["after"]["total"] / 1e6
    big = C["before"]["PySide6"] / 1e6
    other0 = base - big
    e = 1 - (1 - frac) ** 3 if step == 2 else (1.0 if step >= 3 else 0.0)
    big_now = big * (1 - e)
    other_now = other0 + (after - other0) * e  # 残りの部分の実測の差(PYZ の縮み)も反映
    total = other_now + big_now
    px = EXE_PX / base
    stub_w = 54 if e > 0 else 0
    w_other, w_big = other_now * px, big_now * px
    box_w = w_other + w_big + stub_w + 12 + (6 if w_big > 1 and stub_w else 0) + (6 if (w_big > 1 or stub_w) else 0)
    col = "#f14c4c" if e == 0 else "#23d18b" if e >= 1 else "#e0a030"
    segs = f'<div class="seg other" style="width:{w_other:.1f}px"></div>'
    if w_big > 1:
        inner = f'<div class="nm">{target}</div><div class="sz">{big:.0f} MB</div>' if w_big > 230 else ""
        segs += f'<div class="seg qt{" glow" if step == 1 else ""}" style="width:{w_big:.1f}px">{inner}</div>'
    if stub_w:
        segs += f'<div class="seg stub" style="width:{stub_w}px;opacity:{min(1.0, e * 2):.2f}">{t["c_stub"]}</div>'
    body = (
        f'<div class="h">{t["todo"]}</div><div class="tot" style="color:{col}">{total:.1f} MB</div>'
        f'<div class="lab">{t["c_exe"]}</div>'
        + (f'<div class="ghost" style="opacity:{min(1.0, e * 1.5):.2f}">−{base - total:.1f} MB</div>' if e > 0 else "")
        + f'<div class="exe" style="width:{box_w:.1f}px">{segs}</div>'
        f'<div class="under" style="left:46px">{t["c_other"]}</div>'
    )
    if step == 1:
        body += f'<div class="warn" style="left:{46 + w_other + 20:.0f}px">↓ {t["c_unused"]}</div>'
    if step >= 3:
        body += f'<div class="same">{t["c_same"]}</div>'
    cap = t[f"c{step}"].format(big=f"{big:.0f}", base=f"{base:.1f}", after=f"{after:.1f}")
    return doc(body, cap, CONT_CSS)



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
        pg = st
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
            if make_gif(lang, Data(Path(a.data), "en"), out, ffmpeg, Path(t), kd, hd) > MAX_BYTES:
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
