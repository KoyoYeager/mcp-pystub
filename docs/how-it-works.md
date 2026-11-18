# 仕組みと判定 / How it works

[README に戻る](../README.ja.md) | 判定の種類、confidence、3 層の役割と保証、実行の記録、解析アルゴリズム。 / Verdicts, confidence, the three layers, execution trace, and the analysis algorithm.

Python exe ビルド（PyInstaller / Nuitka / cx_Freeze）時にスタブ置換可能なパッケージを自動検出し、最小スタブコードを生成する MCP サーバー。

An MCP server that auto-detects stubbable packages for Python exe builds (PyInstaller / Nuitka / cx_Freeze) and generates minimal stub code to reduce executable size.

## 背景 / Background

Python アプリを exe 化すると、依存ライブラリがモジュールレベルで import する重量パッケージが丸ごと同梱され、exe サイズが膨張する。実際のコードパスで使わないパッケージは最小ダミー（スタブ）で置換すれば大幅なサイズ削減が可能。

When building Python apps into executables, heavy packages imported at module level by dependencies get bundled entirely, bloating the exe size. Replacing unused packages with minimal stubs can significantly reduce size.

## 機能 / Features

| ツール / Tool | 説明 / Description |
|---|---|
| `analyze` | import グラフを解析しスタブ候補を自動検出。C拡張パッケージの間接排除ヒントも出力 / Auto-detect stubbable packages + submodule stub hints for C-extension elimination |
| `graph` | import グラフをノード・エッジで可視化 / Visualize import graph as nodes and edges |
| `check` | 特定パッケージの使用状況を詳細分析 / Deep analysis of a specific package's usage |
| `generate` | パッケージスタブの最小コードを生成 + ビルド手順出力 / Generate minimal stub code + build instructions |
| `generate_submodule` | **C拡張パッケージを間接排除するサブモジュールスタブを生成** / Generate submodule stubs to indirectly eliminate C-extension packages |
| `verify` | 生成スタブに差し替えた状態を、対象環境の python で実際に import して確かめ、通った組を返す（site-packages は書き換えない） / Dynamically verify proposals by importing the graph with stubs in the target interpreter |

> `analyze` の結果は静的解析による**候補**です。判定の仕様は [docs/STUB_SAFETY_SPEC.md](STUB_SAFETY_SPEC.md)。
> 適用前に `verify` で確かめ、ビルド後に実行結果を元と比べてください。

### 判定結果 / Verdicts

判定は 2 段に分かれます（仕様: [docs/STUB_SAFETY_SPEC.md](STUB_SAFETY_SPEC.md) の 1〜6 節と 9 節）。

**静的解析で使用が見つからなかった候補** — エントリーから到達しうるコードに、スタブで動作が変わる使用が見つからなかったもの（保証ではありません。実際に外せるかは下の 3 層の役割を参照）:

| 判定 / Verdict | 意味 / Meaning |
|---|---|
| **stubbable** | 静的解析で、スタブで動作が変わる使用が見つからなかった候補 / Candidate: no use found by static analysis |
| **nofollow** | 保護された import（try/except）だけで参照が見つからなかった、除外の候補 / Candidate: guarded import, no reference found |
| `submodule_stub_hints` | C 拡張を import 閉包から外す、使用が見つからなかったサブモジュールの組 / Submodule stub candidates |
| **required** | スタブ化不可（確実に実行される使用がある、または区別できない） / Cannot stub |

**条件付き**（`conditional` / `conditional_submodule_hints`） — 安全は示せないが、列挙した機能を使わないなら外せる候補。
stubbable には混ざりません。各候補に、推定の削減量・条件（壊れる入口と、プロジェクトが呼ぶ公開 API からの経路）・
証明できなかった理由（名前だけの振り分け / 引数に渡したクラス / 登録デコレータ / 入力次第の分岐）・確認方法
（`verify(..., run_entry=True)` の呼び出し）と `confidence`（high / low）が付きます。

条件付きのスタブは、条件の機能に到達した時点（関数の呼び出し、値の代わりへの添字・真偽・書式・比較・反復・属性、
普通でないクラスの str / 反復など）で、標準エラーに `[mcp-pystub] PystubConditionalStubError: mcp-pystub のスタブです。…`
と原因を書いて **終了コード 70 で即終了**します（`except` で握りつぶせません）。標準エラーが無い窓のない exe では、
原因を exe のあるフォルダ（書けなければ一時フォルダ）の `pystub_stop.log` に書きます。`MCP_PYSTUB_STRICT=0` を付けた時だけ
`PystubConditionalStubError` を投げる動作になります（呼び出し元の `except Exception` 等で握りつぶされうる箇所は条件の
`may_be_swallowed` に出ます）。**止まらない操作**: スタブのクラスそのものの `==`・hash・真偽・repr は元のクラスと同じ動作で、元に無い属性は
`AttributeError` になります（isinstance の判定も止まりません）。この止まり方は、stubbable・サブモジュールのヒントを含む全てのスタブで同じです（nofollow はビルドから外すだけ）。stubbable・サブモジュールのヒントのスタブは、静的解析で参照が見つかったモジュールだけを持つので、それ以外のサブモジュールの import は `ModuleNotFoundError` になります（止まらずに例外になる。条件付き・記録からの候補のスタブは全てのサブモジュールを持ち、使った時点で止まる）。記録からの候補のスタブ（静的解析では required）は、元にある値でも AttributeError になることがあります（例: 01_tqdm の `PIL.Image.NEAREST`。静的解析で参照が見つからなかった名前は持たないため）。条件付きのスタブのクラス（例: fontTools の `TTFont`）は、元に無い属性を聞いても AttributeError にならず、終了コード 70 で止まることがあります（属性の一覧を持たないクラスの代わりのため）。配布物がビルド時のフック探索の入口（entry_points の `pyinstaller40`）を宣言していれば、その関数は空のリストを返します（PyInstaller のビルドが止まらないように）。

**制限**: 子プロセス（multiprocessing・concurrent.futures・subprocess）の中で止まった場合、親が終了コードや例外として
受け取って処理を続けることがあります。即終了なので、書き込み途中のファイルが残り、`atexit` / `finally` は実行されません。
site-packages の `.pth` と `sitecustomize` が起動時に実行するコードは解析の外です。

**confidence**（条件付き候補ごと。仕様 10.2.1）:

| 値 | 意味 |
|---|---|
| `high` | 規則による参考の表示: 入口の列挙が完了し、プロジェクトからの使用が 0 件、受け手の分からない同名の呼び出しも無く、入口へ入る辺が登録・間接呼び出しの仕組みでない。**確定した経路が無いことの保証ではありません**（例: asammdf のシナリオの asammdf.gui は high だが `asammdf.MDF.__init__()` からの経路が出る）。経路は `route_from_project`（確定）と `estimated_routes`（推定）に分けて出す |
| `low` | 条件を守れば外せるが、プロジェクトが入口に届く公開関数を呼んでいる（直接でも、ほかの関数を経てでも）・経路が間接的。解析したエントリーでは変わらなくても、別の入力で終了コード 70 で止まりうる。理由は `confidence_reason`（例: 「プロジェクトが `asammdf.MDF.append()` を呼んでいます。入力によってはここから … pandas に届きます」） |

「推定」は、名前だけで結び付けた呼び出し（受け手の型が分からない `o.plot()` を同名のメソッド全てに結ぶ）、`__del__` のような
暗黙の呼び出し、動的な振り分け（getattr・登録辞書など）のことです。確定した経路は `route_from_project`、推定の経路は
`estimated_routes` に分けて出します。high は上の規則による参考の表示なので、high の候補に確定した経路が出ることもあります
（その時は `confidence_reason` に「プロジェクトから入口へ入る経路があります」と出ます）。

low でも、解析したエントリーで変わらないことは `verify(..., run_entry=True)` で確かめられます。verify が比べるのは
エントリーのプロセスの標準出力（fd 1）のバイト列の全体（子孫のプロセス・atexit・`os.write(1, …)` の出力も含む。長さとハッシュで比べる）と
終了コードだけで、標準エラー・ファイルへの出力・通信は比べません。エントリーは `sys.argv = [エントリー] + entry_args`、
作業フォルダ `cwd`（既定はエントリーのあるフォルダ）で実行するので、引数や入力ファイルが要るエントリーでは `entry_args` /
`cwd` を指定してください。基準（スタブなし）の実行が失敗した時は何も通さず `baseline_failed` に理由を返します。

`analyze` の結果の先頭には `summary`（使用が見つからなかった候補の推定削減 MB・conditional(high) の MB と件数・推奨の一覧・次の手順）が
付きます。1 MB 未満の条件付き候補は既定で省き（`conditional_omitted_small` に件数）、`include_small_conditional=True`
で全件を出します。推定サイズ（`estimated_size_mb` / `estimated_savings_mb`）は**インストール済みのファイルの大きさ**で、
exe の削減量ではありません（実際の削減は PyInstaller の同梱範囲と圧縮で変わります）。

## できること / What It Can Do

- **ライブラリ非固定の汎用検出**: AST 構造のみで判定。ハードコードなし
- **関数レベルの使用追跡**: `mdf.get()` は使うが `mdf.to_dataframe()` は使わない → pandas は stubbable
- **Call と参照の区別**: `isinstance(x, pd.DataFrame)` は stub-safe、`pd.DataFrame(data)` は実使用
- **クラス継承の検出**: `class User(BaseModel)` → pydantic は required
- **module-level 呼び出し検出**: import 時に実行されるコードを追跡
- **try/except 保護の自動検出**: 保護された import は `nofollow` と判定
- **スタブコード自動生成**: 参照シンボルのみの最小スタブ + ビルド手順（バックアップ・復元・検証）
- **C 拡張の自動検出**: .pyd / .so を含むパッケージは直接スタブ化不可と判定
- **C 拡張の間接排除** *(v0.2 new)*: C拡張パッケージを import しているサブモジュールが未使用なら、そのサブモジュールをスタブ化して C拡張を排除可能。12_asammdf で asammdf.gui をスタブにして PySide6・lxml を外し、exe 240.1 → 36.9 MB（Claude Code に頼んだ記録では 252.0 → 38.9 MB。[実証データ](results.md)）
- **復元安全設計**: 元ファイルは site-packages の外の `_pystub_bak/<項目名>/` へ移動（pip uninstall しない）。
  項目ごとの領域なので、パッケージ全体とサブモジュールのスタブを重ねても元ファイルを失いません（内側が適用中なら
  外側の適用は断ります）。二重適用・中断にも耐え、復元後は verify_commands が `__file__` と RECORD のハッシュで確かめます
- **PyInstaller フック情報**: 無効化が必要なフックファイルを通知

## 3 層の役割と保証 / Three layers

| 層 | 役割 | 保証 |
|---|---|---|
| 静的解析（`analyze`） | 候補を出し、サイズ順に並べ、理由（使われ方・条件・confidence）を説明する | 保証しない（提案） |
| 動的解析（`verify(run_entry=True)` と実行の記録） | エントリーを実際に動かし、候補が実行されたか（記録）と、スタブに差し替えても動作が変わらないか（verify）を確かめる | **標準出力をファイルにつないだ普段の実行（`python entry.py 引数… > out.txt`）と、「普段の実行との違い」の表の範囲で同じ条件で動かした入力では、標準出力と終了コードが変わらない**。比べるのは標準出力のバイト列の全体と終了コードだけ（標準エラー・ファイル・通信は比べない）。確かめた組（`verified_sets`）をそのまま適用した場合だけ。親子の対応が合わない子プロセス（下の「記録と差し込みの制限」（[安全性と制限](safety.md)））、`.pth` で起動時に読み込まれるパッケージは確認済み（実行）にしない。確認済み（実行）は、[安全性と制限](safety.md)の「普段の実行との違い」の表の範囲で、動かした入力について確かめた結果です。配布前に、ビルドした exe を普段のやり方で試してください。 |
| 実行時（止まるスタブ） | 動かしていない経路でスタブに触れたら、原因を出して終了コード 70 で止まる（全てのスタブ。nofollow はビルドから外すだけ） | 黙って結果が変わらない（「止まらない操作」と[安全性と制限](safety.md)の制限を除く） |

**保証の締め**: 確認済み（実行）は、[安全性と制限](safety.md)の「普段の実行との違い」の表の範囲で、動かした入力について確かめた結果です。配布前に、ビルドした exe を普段のやり方で試してください。

**確認の手順**: (1) `verify`（既定は import だけ: 別の子プロセスでグラフ上のモジュールを import する。各モジュールの直下のコード（import 時のコード）は動くが、エントリーのスクリプトは動かさない（エントリーを import するローカルのモジュールは対象から外し、import の途中でエントリーのファイルを開く操作は監査フックで止めて note に出す。Python の `open` を通る読み込みは、別名（短いパス名・`\\?\`・UNC・ハードリンク）でも、ファイルの同一性で見分けて止めます。OS のコピー API（`shutil.copy2` の CopyFile2 など）、ctypes、外部コマンド（`cmd /c type` 等）での読み込みと、それでコピーしたファイル経由の import（zipimport 等）は止められません））→ (2) スタブを適用してビルドし、普段のやり方で exe を
試す（全てのスタブ（stubbable・サブモジュールのヒント・条件付き・記録からの候補）は、使われた時点で原因を出して終了コード 70 で止まります（except で握りつぶせません。MCP_PYSTUB_STRICT=0 の時だけ例外）。ただし この文書の「止まらない操作」（スタブのクラスそのものの ==・hash・真偽・repr、isinstance の判定、元に無い属性の AttributeError など）は止まりません）→（任意）エントリーを動かしても安全な時だけ `run_entry=True` や
実行の記録でより強く確かめる。

> **警告**: `run_entry=True` と実行の記録は、対象のプログラムを実際に実行します。ファイルの書き込み・削除、通信、機器の操作などの
> 副作用が起きえます。AI エージェントは、利用者が明示的に求めた時だけ指定してください。GUI や常駐型のプログラムは終了しないので
> timeout（子プロセス 1 回あたりの秒数。既定 900）で打ち切られます。打ち切った時と、エントリーが終わった後は、孫を含む
> プロセスの木を止めます（Windows は Job Object、それ以外はプロセスグループ）。verify は `MPLBACKEND` などを付けないので、
> 窓を開くエントリーは、利用者が普段どおりの環境変数（例: `MPLBACKEND=Agg`・`QT_QPA_PLATFORM=offscreen`）や引数を付けて
> verify を呼んでください（記録もその環境で取ります）。

## 実行の記録 / Execution trace（任意）

利用者が自分の管理下（試験用のデータ・テストの実行など）でプログラムを動かし、どのパッケージが import され、どの関数が
実行されたかを記録できます。**対象のプログラムを実際に実行します**（副作用が起きえます）。

```bash
python -m mcp_pystub.trace --out trace.json entry.py 引数...            # スクリプト
python -m mcp_pystub.trace --out trace.json -m pytest tests/             # -m 形式（pytest など）
python -m mcp_pystub.trace --out trace.json --append entry.py 別の引数   # 複数回の実行を合算
```

対象環境の python で動かします。サーバーの環境の site-packages を `PYTHONPATH` に通すと、対象環境に別の版の
パッケージ（mcp など）が見えて壊れるので、**mcp_pystub だけを置いたフォルダ**を使ってください（trace は mcp を使いません）:

```bash
# 1 回だけ: mcp_pystub だけを置いたフォルダを作る（依存は入れない。wheel か、このリポジトリのフォルダを指定）
python -m pip install --no-deps --target C:\tools\pystub_trace mcp_pystub-0.3.0-py3-none-any.whl
# 記録を取る（対象の venv の python で）
set PYTHONPATH=C:\tools\pystub_trace
C:\project\.venv\Scripts\python.exe -m mcp_pystub.trace --out trace.json entry.py 引数...
```

Python 3.12 以降は
`sys.monitoring`（関数の開始イベント）、それより前は `sys.setprofile` + `threading.setprofile` で記録します。

- `analyze(..., trace_files=[...])`: 記録を読むだけで実行はしません。各候補に `observed`（`not_imported` / `import_only` /
  `executed`（件数と代表的な関数名）/ `unknown_native`）を付けます。静的解析の候補が記録で `executed` なら **記録と矛盾**
  （`trace_conflicts`）として推奨から外し、静的解析では required だが記録で `not_imported` / `import_only` のものは
  **記録からの候補**（`trace_candidates`、verify で確かめる）に出します。
- `verify(..., run_entry=True, record=True)`: 基準（スタブなし）のエントリー実行で記録も取ります（追加の実行はしません）。
  `trace_files=[...]` で自分で作った記録も渡せます。各項目に `statuses` と `status_summary` が付きます:

| 状態 | 意味 |
|---|---|
| 確認済み（実行） | 実行の記録で未実行（`not_imported` / `import_only`）、かつ `run_entry=True` の差し替え実行を通った |
| import 確認済み | スタブに差し替えた import（と、指定があればエントリーの実行）を通った。実行の記録での確認は無い・記録では実行されていた |
| 未確認 | 通らなかった・確かめられなかった |

`verified_sets` に、どの組で確かめたかが出ます（passed の組と、状態が混ざる時は確認済み（実行）の項目だけの組でもう 1 回
エントリーを動かした結果。通らなければその項目は import 確認済みに下げる）。**確かめた組をそのまま適用してください。
一部だけを適用するなら、その組で verify を回し直してください**。

記録の制限: 子孫のプロセスは、起動用の仕組みが効くもの（環境を受け継ぐ python）だけ記録します（[安全性と制限](safety.md)の「普段の実行との違い」の表）。動かした入力の範囲しか分かりません。エントリーは実際に動きます（ファイルの
書き込み・通信などの副作用）。拡張モジュール（.pyd / .so）の中の実行は記録できません（拡張モジュールを含み、関数の実行が
記録に無いものは `unknown_native` で、未実行とは扱いません）。import の途中に自分のパッケージの関数を呼ぶだけのもの
（`V = _setup()` 等）は `import_only` に数えます（別のパッケージの import の途中に呼ばれたものは実行に数えます）。

## 解析アルゴリズム / Analysis Algorithm

1. **Import 抽出**: `ast.parse()` で各ファイルの import 文を解析（module-level / function-level / try-except 保護を区別）
2. **Import グラフ構築**: エントリーポイントから BFS で依存関係を再帰解決（stdlib / third_party / local を自動分類）
3. **使用分析**: gateway 関数（依存ライブラリ内でパッケージを**呼び出す**関数）を特定し、プロジェクトコードがそれを呼んでいるか追跡。名前参照のみ（isinstance, 型アノテーション）は stub-safe として除外
4. **Module-level 検出**: import 時にパッケージの関数が呼ばれる場合は required に格上げ
5. **サブモジュール間接排除** *(v0.2)*: C拡張パッケージを import しているサブモジュールを特定し、プロジェクトが直接 import していない + re-export シンボルを呼んでいない場合にスタブ化ヒントを出力
