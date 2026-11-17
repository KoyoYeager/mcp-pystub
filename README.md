<!-- mcp-name: io.github.KoyoYeager/pystub -->

# mcp-pystub

Python exe ビルド（PyInstaller / Nuitka / cx_Freeze）時にスタブ置換可能なパッケージを自動検出し、最小スタブコードを生成する MCP サーバー。

---

An MCP server that auto-detects stubbable packages for Python exe builds (PyInstaller / Nuitka / cx_Freeze) and generates minimal stub code to reduce executable size.

## 背景 / Background

Python アプリを exe 化すると、依存ライブラリがモジュールレベルで import する重量パッケージが丸ごと同梱され、exe サイズが膨張する。実際のコードパスで使わないパッケージは最小ダミー（スタブ）で置換すれば大幅なサイズ削減が可能。

When building Python apps into executables, heavy packages imported at module level by dependencies get bundled entirely, bloating the exe size. Replacing unused packages with minimal stubs can significantly reduce size.

### 実証データ / Verified Results

測定条件: Windows 11、Python 3.13、PyInstaller（onefile）、各シナリオの `entry.py`（`C:\tmp\pystub-e2e` の合成シナリオ）。
exe の出力が元と一致することと、site-packages の復元が元と一致することを確かめた値です（第 9〜10 回の独立検証で再現）。

| シナリオ | スタブなし | 適用後 | 適用した候補 |
|---|---|---|---|
| 12_asammdf | 240.1 MB | **36.9 MB** | 確認済み（実行）の `asammdf.gui`（PySide6・lxml を排除）だけ |
| 12_asammdf | 240.1 MB | **25.1 MB** | 静的解析の候補を全部（pandas + asammdf.gui） |
| 03_networkx | 76.7 MB | **40.8 MB** | scipy（記録からの候補）・fontTools・pandas |
| 01_tqdm | 47.7 MB | **32.7 MB** | pandas・fontTools・PIL（記録からの候補） |
| 02_openpyxl | 26.2 MB | **20.3 MB** | openpyxl.drawing.image（PIL を排除） |
| 15_pillow | 25.7 MB | **14.4 MB** | numpy |

## 機能 / Features

| ツール / Tool | 説明 / Description |
|---|---|
| `analyze` | import グラフを解析しスタブ候補を自動検出。C拡張パッケージの間接排除ヒントも出力 / Auto-detect stubbable packages + submodule stub hints for C-extension elimination |
| `graph` | import グラフをノード・エッジで可視化 / Visualize import graph as nodes and edges |
| `check` | 特定パッケージの使用状況を詳細分析 / Deep analysis of a specific package's usage |
| `generate` | パッケージスタブの最小コードを生成 + ビルド手順出力 / Generate minimal stub code + build instructions |
| `generate_submodule` | **C拡張パッケージを間接排除するサブモジュールスタブを生成** / Generate submodule stubs to indirectly eliminate C-extension packages |
| `verify` | 生成スタブに差し替えた状態を、対象環境の python で実際に import して確かめ、通った組を返す（site-packages は書き換えない） / Dynamically verify proposals by importing the graph with stubs in the target interpreter |

> `analyze` の結果は静的解析による**候補**です。判定の仕様は [docs/STUB_SAFETY_SPEC.md](docs/STUB_SAFETY_SPEC.md)。
> 適用前に `verify` で確かめ、ビルド後に実行結果を元と比べてください。

### 判定結果 / Verdicts

判定は 2 段に分かれます（仕様: [docs/STUB_SAFETY_SPEC.md](docs/STUB_SAFETY_SPEC.md) の 1〜6 節と 9 節）。

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
`AttributeError` になります（isinstance の判定も止まりません）。この止まり方は、stubbable・サブモジュールのヒントを含む全てのスタブで同じです（仕上げの回で統一。nofollow はビルドから外すだけ）。stubbable・サブモジュールのヒントのスタブは、静的解析で参照が見つかったモジュールだけを持つので、それ以外のサブモジュールの import は `ModuleNotFoundError` になります（止まらずに例外になる。条件付き・記録からの候補のスタブは全てのサブモジュールを持ち、使った時点で止まる）。配布物がビルド時のフック探索の入口（entry_points の `pyinstaller40`）を宣言していれば、その関数は空のリストを返します（PyInstaller のビルドが止まらないように）。

**制限**: 子プロセス（multiprocessing・concurrent.futures・subprocess）の中で止まった場合、親が終了コードや例外として
受け取って処理を続けることがあります。即終了なので、書き込み途中のファイルが残り、`atexit` / `finally` は実行されません。
site-packages の `.pth` と `sitecustomize` が起動時に実行するコードは解析の外です。

**confidence**（条件付き候補ごと。仕様 10.2.1）:

| 値 | 意味 |
|---|---|
| `high` | 規則による参考の表示（8c0a487 の規則で凍結）: 入口の列挙が完了し、プロジェクトからの使用が 0 件、受け手の分からない同名の呼び出しも無く、入口へ入る辺が登録・間接呼び出しの仕組みでない。**確定した経路が無いことの保証ではありません**（例: 12 の asammdf.gui は high だが `asammdf.MDF.__init__()` からの経路が出る）。経路は `route_from_project`（確定）と `estimated_routes`（推定）に分けて出す |
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
- **C 拡張の間接排除** *(v0.2 new)*: C拡張パッケージを import しているサブモジュールが未使用なら、そのサブモジュールをスタブ化して C拡張を排除可能。12_asammdf で asammdf.gui をスタブにして PySide6・lxml を外し、exe 240.1 → 36.9 MB（上の実証データ）
- **復元安全設計**: 元ファイルは site-packages の外の `_pystub_bak/<項目名>/` へ移動（pip uninstall しない）。
  項目ごとの領域なので、パッケージ全体とサブモジュールのスタブを重ねても元ファイルを失いません（内側が適用中なら
  外側の適用は断ります）。二重適用・中断にも耐え、復元後は verify_commands が `__file__` と RECORD のハッシュで確かめます
- **PyInstaller フック情報**: 無効化が必要なフックファイルを通知

## できないこと・制限事項 / Limitations

- **C 拡張パッケージ**（.pyd / .so）は直接スタブ化すると C 拡張が欠落するため required 判定。ただし `generate_submodule` で**間接排除が可能**
- **動的 import**（`importlib.import_module(変数)` / `__import__(変数)`）は静的解析で追跡不可。プロジェクトのコードに
  あれば安全側（required）、依存ライブラリの中なら warnings にモジュールと行を出し、判定の前提とします（仕様 5 節）
- **PyInstaller カスタムフック**: スタブと衝突する場合がありフックの手動無効化が必要
- **遅延初期化パターン**: `__init__` で直接呼ばず後のメソッドで使うパッケージは検出精度が下がる（安全側で required に判定）
- **pip 名と import 名の不一致**: `python-dateutil` → `dateutil` 等のマッピングは未対応
- **ランタイムの条件分岐**: `if sys.version_info < (3, 11)` 等の実行環境で決まる分岐は、両方の枝を「確実に実行される」
  側に倒します（安全側。削減を取りこぼすことがあります）

### 3 層の役割と保証 / Three layers

| 層 | 役割 | 保証 |
|---|---|---|
| 静的解析（`analyze`） | 候補を出し、サイズ順に並べ、理由（使われ方・条件・confidence）を説明する | 保証しない（提案） |
| 動的解析（`verify(run_entry=True)` と実行の記録） | エントリーを実際に動かし、候補が実行されたか（記録）と、スタブに差し替えても動作が変わらないか（verify）を確かめる | **標準出力をファイルにつないだ普段の実行（`python entry.py 引数… > out.txt`）と、「普段の実行との違い」の表の範囲で同じ条件で動かした入力では、標準出力と終了コードが変わらない**。比べるのは標準出力のバイト列の全体と終了コードだけ（標準エラー・ファイル・通信は比べない）。確かめた組（`verified_sets`）をそのまま適用した場合だけ。親子の対応が合わない子プロセス（下の「記録と差し込みの制限」）、`.pth` で起動時に読み込まれるパッケージは確認済み（実行）にしない。確認済み（実行）は、README の「普段の実行との違い」の表の範囲で、動かした入力について確かめた結果です。配布前に、ビルドした exe を普段のやり方で試してください。 |
| 実行時（止まるスタブ） | 動かしていない経路でスタブに触れたら、原因を出して終了コード 70 で止まる（全てのスタブ。nofollow はビルドから外すだけ） | 黙って結果が変わらない（「止まらない操作」と下の制限を除く） |

**保証の締め**: 確認済み（実行）は、README の「普段の実行との違い」の表の範囲で、動かした入力について確かめた結果です。配布前に、ビルドした exe を普段のやり方で試してください。

**確認の手順**: (1) `verify`（既定は import だけ: 別の子プロセスでグラフ上のモジュールを import する。各モジュールの直下のコード（import 時のコード）は動くが、エントリーのスクリプトは動かさない（エントリーを import するローカルのモジュールは対象から外し、import の途中でエントリーのファイルを開く操作は監査フックで止めて note に出す。Python の `open` を通る読み込みは、別名（短いパス名・`\\?\`・UNC・ハードリンク）でも、ファイルの同一性で見分けて止めます。OS のコピー API（`shutil.copy2` の CopyFile2 など）、ctypes、外部コマンド（`cmd /c type` 等）での読み込みと、それでコピーしたファイル経由の import（zipimport 等）は止められません））→ (2) スタブを適用してビルドし、普段のやり方で exe を
試す（全てのスタブ（stubbable・サブモジュールのヒント・条件付き・記録からの候補）は、使われた時点で原因を出して終了コード 70 で止まります（except で握りつぶせません。MCP_PYSTUB_STRICT=0 の時だけ例外）。ただし README の「止まらない操作」（スタブのクラスそのものの ==・hash・真偽・repr、isinstance の判定、元に無い属性の AttributeError など）は止まりません）→（任意）エントリーを動かしても安全な時だけ `run_entry=True` や
実行の記録でより強く確かめる。

> **警告**: `run_entry=True` と実行の記録は、対象のプログラムを実際に実行します。ファイルの書き込み・削除、通信、機器の操作などの
> 副作用が起きえます。AI エージェントは、利用者が明示的に求めた時だけ指定してください。GUI や常駐型のプログラムは終了しないので
> timeout（子プロセス 1 回あたりの秒数。既定 900）で打ち切られます。打ち切った時と、エントリーが終わった後は、孫を含む
> プロセスの木を止めます（Windows は Job Object、それ以外はプロセスグループ）。verify は `MPLBACKEND` などを付けないので、
> 窓を開くエントリーは、利用者が普段どおりの環境変数（例: `MPLBACKEND=Agg`・`QT_QPA_PLATFORM=offscreen`）や引数を付けて
> verify を呼んでください（記録もその環境で取ります）。

### 実行の記録 / Execution trace（任意）

**普段の実行との違い**（第 9 回。verify のエントリー実行・import の確認・`python -m mcp_pystub.trace` の子プロセス）:

| 項目 | 普段の `python entry.py 引数… > out.txt` | verify / 記録 | 扱い |
|---|---|---|---|
| 起動オプション | なし | なし（`-X utf8` を付けない） | 無くした |
| PYTHONPATH | 利用者の値 | 先頭に起動用フォルダ + 利用者の値（起動後、`sys.path` からは起動用フォルダを外す） | 残る違い（起動用） |
| 起動用の環境変数 | なし | `MCP_PYSTUB_BOOT`（子孫では値の末尾に `#d`） | 残る違い（起動用） |
| PYTHONIOENCODING / MPLBACKEND / QT_QPA_PLATFORM / PYTHONNOUSERSITE / PYTHONDONTWRITEBYTECODE | 利用者の値 | verify を呼ぶ側（MCP サーバーのプロセス）の値のまま（付けない・消さない） | 無くした（ただし MCP サーバーを普段と違う環境変数で起動していると、その違いはそのまま入る。文書） |
| 標準出力 | パイプ / ファイル / コンソール | ファイル（`sys.stdout` は差し替えない。`fileno()`・`encoding`・`reconfigure()` はファイルにつないだ普段の実行と同じ） | 文書: 普段がパイプやコンソールの場合とは種類が違う（`isatty` や FIFO かどうかで分岐するプログラムは結果が変わりうる） |
| 標準エラー | 端末・ファイル | ファイル（比べない。表示用） | 文書（比べない） |
| 標準入力 | 端末 | 空（DEVNULL） | 文書 |
| `sys.argv` / `__main__` / `__file__` / `sys.path[0]` | `python entry.py` のとおり | 同じ（エントリーを直接起動） | 無くした |
| パッケージの中のエントリー（相対 import・`__main__.py`） | 利用者の起動のしかた | `python -m pkg.mod` で起動する | 文書 |
| `import sitecustomize` | 普段は ImportError（無ければ） | `import sitecustomize` が成功する（起動用のもの。元の sitecustomize があれば続けて読む） | 文書 |
| atexit | 利用者の登録だけ | atexit の登録が 1 つ増える（記録を書く） | 文書 |
| `sys._pystub_boot` | 無い | ある（起動用の状態） | 文書 |
| `sys.path_importer_cache` | 普段の件数 | 起動用フォルダの分などで件数が違いうる | 文書 |
| site-packages のファイル | 適用後は元のパッケージのファイルが無い | verify の時は元のパッケージのファイルがディスクに残る（スタブは meta path finder で差し込む） | 文書: `importlib.metadata.files()` などで import せずにパッケージ内のファイルを読むプログラムは、適用後に結果が変わりうる |
| Job Object | 入っていない | 入っている（プロセスの木を止めるため。venv でない python でも同じ） | 文書: Job に入っているかどうかで分岐するプログラムは結果が変わりうる |
| 作業フォルダ | 利用者の場所 | `cwd`（既定はエントリーのあるフォルダ） | 文書（`cwd` で指定） |
| 先に import されるモジュール | site と `.pth` の分 | 同じ + 起動用の sitecustomize の分（json・importlib.abc 等の標準ライブラリ。記録の時は mcp_pystub.trace） | 文書 |
| 監査フック・`os._exit` の置き換え | なし | `sys.addaudithook` 1 つ（プロセスの起動を数える。import の確認ではエントリーのファイルを開く操作を止める）、`os._exit` を包む（記録を書いてから終わる） | 文書 |
| 記録（`sys.monitoring` / `sys.setprofile`） | なし | 記録の時だけ（遅くなる。tool id を 1 つ使う） | 文書 |
| 子孫のプロセス | 環境を受け継ぐ | 同じ。Python の監査イベントで分かる起動（subprocess・os.spawn*・os.exec*・os.system・CreateProcess）を、親子の対応で確かめます（プロセスごとに、起動した数と、自分を親とする登録済みの子の数が合うか）。合わなければ全項目を確認済み（実行）にしません。`ctypes` で OS の API を直接呼ぶ起動、COM / WMI 経由の起動は検出できません | 既知の経路だけ検出（完全ではない） |
| `.pth` が起動時に読み込むパッケージ | 本物 | 本物（スタブが効かない） | 検出（`loaded_at_startup`） |
| 終了の後 | 孫は残りうる | 孫を含むプロセスの木を止める | 文書 |

**記録と差し込みの制限**: 起動用の `sitecustomize.py` は site-packages の `.pth` より後に読まれるので、`.pth` が起動時に読み込む
パッケージにはスタブが効かず、記録でも確かめられません（`loaded_at_startup`）。子プロセスは、Python の監査イベントで分かる起動（subprocess・os.spawn*・os.exec*・os.system・CreateProcess）を、親子の対応で確かめます（プロセスごとに、起動した数と、自分を親とする登録済みの子の数が合うか）。合わなければ全項目を確認済み（実行）にしません。`ctypes` で OS の API を直接呼ぶ起動、COM / WMI 経由の起動は検出できません。
`-E` / `-I` / `-S` で起動された python、環境を消したり PYTHONPATH を上書きしたりして起動した子、python 以外のプログラム（cmd など）
には起動用の仕組みが効かないので、親子の対応が合わず、確認済み（実行）にしません（その先の孫の python が登録されていても相殺しません。
サブインタープリターは子孫として数えず、その中の起動の数を親のプロセスに足します）。検出は既知の経路に対するもので、完全ではありません。
3.12 より前は `threading.setprofile` を使うので、
`_thread.start_new_thread` で直接起動したスレッドの中は記録されません。ファイルの無いコードのうち、所属のモジュールを
決められないもの（実在しない `__name__` のグローバルで exec し、最初の引数も所属を示さない関数や、実在しないファイル名で
compile した関数）は記録から所属のパッケージに数えられません（verify の差し替え実行では却下されます）。
**記録は普段と同じ環境（環境変数・設定ファイル・入力）で取ってください**（試験で `MPLBACKEND=Agg` を付けると、普段使う GUI の
バックエンドが未実行に見えます）。

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

記録の制限: 子孫のプロセスは、起動用の仕組みが効くもの（環境を受け継ぐ python）だけ記録します（上の表）。動かした入力の範囲しか分かりません。エントリーは実際に動きます（ファイルの
書き込み・通信などの副作用）。拡張モジュール（.pyd / .so）の中の実行は記録できません（拡張モジュールを含み、関数の実行が
記録に無いものは `unknown_native` で、未実行とは扱いません）。import の途中に自分のパッケージの関数を呼ぶだけのもの
（`V = _setup()` 等）は `import_only` に数えます（別のパッケージの import の途中に呼ばれたものは実行に数えます）。

### 安全性の設計方針 / Safety Policy

使用が見つからなかった候補（stubbable / nofollow / submodule_stub_hints）・条件付きとも、**静的解析による提案**です
（[仕様](docs/STUB_SAFETY_SPEC.md) 5 節の前提のもと）。**適用前に `verify(..., run_entry=True)` で、解析したエントリーが
変わらないことを確かめてください**。保証の最後の拠り所は verify と、ビルド後の実行結果の比較です。判定に迷う場合は
required（安全側）に倒し、逆方向の誤判定（本当は stubbable なのに required）は許容します。

条件付きは、条件を破ると原則として終了コード 70 で止まります。ただし、値の代わりに対する `isinstance` / `type()` /
`callable` / `dir()` / `vars()`、スタブのクラスの `issubclass` / `__mro__` / `dir()` / `vars()` / `dataclasses.is_dataclass`、外部の基底から
継承した属性では、止まる前に結果が元と変わりえます（Python の仕組み上スタブ側で止められない）。元に無い属性のエラー文も
元と違います。

## 解析アルゴリズム / Analysis Algorithm

1. **Import 抽出**: `ast.parse()` で各ファイルの import 文を解析（module-level / function-level / try-except 保護を区別）
2. **Import グラフ構築**: エントリーポイントから BFS で依存関係を再帰解決（stdlib / third_party / local を自動分類）
3. **使用分析**: gateway 関数（依存ライブラリ内でパッケージを**呼び出す**関数）を特定し、プロジェクトコードがそれを呼んでいるか追跡。名前参照のみ（isinstance, 型アノテーション）は stub-safe として除外
4. **Module-level 検出**: import 時にパッケージの関数が呼ばれる場合は required に格上げ
5. **サブモジュール間接排除** *(v0.2)*: C拡張パッケージを import しているサブモジュールを特定し、プロジェクトが直接 import していない + re-export シンボルを呼んでいない場合にスタブ化ヒントを出力

## インストール / Installation

```bash
pip install mcp-pystub
```

### 依存パッケージ / Dependencies

- `mcp>=1.0.0,<2` - Model Context Protocol SDK（2.x は FastMCP の API が変わったため未対応）
- 解析エンジンは Python 標準ライブラリのみ使用（ast, importlib, pathlib）

## 使い方 / Usage

### MCP サーバーとして起動 / Run as MCP server

```bash
mcp-pystub
```

### Claude Desktop / Claude Code 設定

```json
{
  "mcpServers": {
    "pystub": {
      "command": "mcp-pystub"
    }
  }
}
```

### ツール使用例 / Tool Examples

#### analyze

12_asammdf（`C:\tmp\pystub-e2e\12_asammdf`）の実際の出力の抜粋:

```
入力 / Input:
  entry_point: "C:/tmp/pystub-e2e/12_asammdf/proj/entry.py"
  python_path: "C:/tmp/pystub-e2e/12_asammdf/venv/Lib/site-packages"

出力 / Output（抜粋）:
  {
    "summary": {"static_candidate_size_mb": 0, "conditional_high_savings_mb": 646.7, "conditional_high_count": 1,
                "recommendations": [...], "next_steps": [...], "note": "...", "closing_note": "...", "size_note": "..."},
    "stubbable": [],
    "required": [
      {"package_name": "PySide6", "estimated_size_mb": 646.7, "reason": "...", "submodule_stubs": []},
      ...
    ],
    "nofollow": [],
    "submodule_stub_hints": [],
    "conditional": [
      {"package_name": "pandas", "estimated_savings_mb": 37.0, "confidence": "low", "confidence_reason": "...",
       "conditions": [...],
       "how_to_check": "verify(..., packages=['pandas'], submodules=[], nofollow=[], run_entry=True, record=True)"}
    ],
    "conditional_submodule_hints": [
      {"submodule": "asammdf.gui", "target_package": "PySide6", "estimated_savings_mb": 646.7, "confidence": "high",
       "conditions": [{"entry": "asammdf.gui 以下を import・使用しない", "why_unproven": "スタブにするサブモジュール自身の中の入口"}, ...],
       "how_to_check": "verify(..., packages=[], submodules=['asammdf.gui=>PySide6'], nofollow=[], run_entry=True, record=True)"},
      {"submodule": "asammdf.gui", "target_package": "lxml", "estimated_savings_mb": 8.5, "confidence": "low", ...}
    ],
    "verify_status": "未実施",
    "analysis_time_ms": 18082
  }
```

サブモジュールのヒントは、確かめられる候補なら `submodule_stub_hints`、条件付きなら `conditional_submodule_hints` に出ます
（required の `submodule_stubs` は、この例のように空のことがあります）。同じパッケージが stubbable と conditional の両方に
載ることはありません。

#### verify

```
入力 / Input:
  entry_point, python_path, packages=["pandas"], submodules=["asammdf.gui=>PySide6"], nofollow=[],
  run_entry=True, timeout=120

出力 / Output（抜粋）:
  {"passed": {"packages": [...], "submodules": [...], "nofollow": [...]},
   "rejected": [{"item": "...", "reason": "...", "module": "...", "error": "..."}],
   "unresolved": [...],          # 原因を特定できなかった項目（passed には入れない）
   "entry_matched": true,        # run_entry=True のとき、エントリーの標準出力（バイト列の全体）と終了コードが元と一致したか
   "status_summary": {"確認済み（実行）": [...], "import 確認済み": [...], "未確認": [...]},
   "verified_sets": [...],       # どの組で確かめたか（そのまま適用する）
   "child_processes": 0, "untraced_child_processes": 0,
   "entry_conditions": {...},    # エントリーを起動したコマンド・作業フォルダ・普段との違い
   "note": "..."}
```

#### generate

```
入力 / Input:
  entry_point: "C:/project/converter.py"
  package_name: "pandas"

出力 / Output:
  {
    "files": {
      "pandas/__init__.py": "...",
      "pandas/core/api.py": "class DataFrame: pass\nclass Series: pass\n..."
    },
    "original_file_count": 2980,
    "stub_file_count": 266,
    "stub_total_bytes": 209530,
    "build_instructions": {
      "backup_commands": ["# 元ファイルを .../Lib/_pystub_bak/pandas/ へ移動", "..."],
      "install_commands": ["# _stubs/ のスタブを配置", "..."],
      "uninstall_commands": ["# 方法1: バックアップから復元", "...", "# 方法2: pip install --force-reinstall --no-deps pandas==<版>"],
      "verify_commands": ["python -c \"import pandas; ...__file__...\"", "# RECORD のハッシュ照合"],
      "hook_disable": ["# リネームして無効化: hook-pandas*.py → .disabled"]
    }
  }
```

#### generate_submodule *(v0.2 new)*

```
入力 / Input:
  entry_point: "C:/project/converter.py"
  parent_package: "asammdf"
  submodule: "asammdf.gui"

出力 / Output:
  {
    "parent_package": "asammdf",
    "submodule": "asammdf.gui",
    "files": {
      "asammdf/gui/__init__.py": "\"\"\"Auto-generated stub...\"\"\"\ndef plot(*args, **kwargs): ..."
    },
    "eliminated_packages": ["PySide6", "scipy", "lxml"],
    "original_size_bytes": 5907331,
    "stub_size_bytes": 144,
    "build_instructions": {
      "backup_commands": ["# .../asammdf/gui を .../Lib/_pystub_bak/asammdf.gui/ へ移動（親のスタブが適用中なら何もしない）"],
      "install_commands": ["# _stubs/asammdf/gui を配置"],
      "uninstall_commands": ["# バックアップから戻す（この項目の領域があるときだけ）"],
      "verify_commands": ["python -c \"import asammdf; ...__file__...\"", "# RECORD のハッシュ照合"]
    }
  }
```

## パフォーマンス / Performance

`C:\tmp\pystub-e2e` の 15 シナリオで、analyze は 1 シナリオ 0.7〜23 秒（第 7 回の測定。独立検証の再現では 2〜21 秒程度。第 1〜3 回の修正前の約 2 倍）、
ピークメモリは最大 401 MB（11_sklearn。ほかは 400 MB 以下）。実行の記録の負荷は、記録しない実行の 1.3〜3.3 倍（10 シナリオ。実行が 0.1 秒程度の短い例ほど倍率が大きい）。

## テスト / Testing

```bash
python -m pytest tests/ -v
```

### テスト実績

| テスト種別 | 結果 |
|-----------|------|
| ユニットテスト（tests/、攻撃シナリオの回帰を含む） | 全通過（件数は下の変更履歴に書く） |
| 実ビルド（上の実証データの 6 本） | exe の出力一致・復元一致 |

## ライセンス / License

MIT

## 変更履歴 / Changelog

### 0.3.0（未公開）

- テスト: tests/ の全件（358 件）が通過（仕上げの回の時点）
- 全てのスタブ（stubbable・サブモジュールのヒントを含む）を、使われたら原因を出して終了コード 70 で止まる形にそろえた
  （nofollow はビルドから外すだけ）
- 出力キーの改名（未公開のため）: summary の `proven_savings_mb` → `static_candidate_size_mb`、
  conditional の `unproven_because` → `conditional_because`
- パッケージ名を `src` から `mcp_pystub` に変えた（`python -m mcp_pystub.trace` など）
- 判定を 3 層に分けた: 静的解析（候補を出すだけ。保証ではない）・動的解析（`verify`、実行の記録）・実行時（止まるスタブ、終了コード 70）
- 追加: `verify` ツール（import だけの確認が既定。`run_entry=True`・`record=True`・`trace_files`・`entry_args`・`cwd`・`timeout`）、
  実行の記録（`python -m mcp_pystub.trace`）、条件付きの候補（`conditional` / `conditional_submodule_hints`、`confidence`）
- 出力キーの追加: `summary`・`conditional`・`conditional_submodule_hints`・`conditional_omitted_small`・`verify_status`・`next_steps`・
  `trace_conflicts`・`trace_candidates`（analyze）、`status_summary`・`statuses`・`verified_sets`・`baseline_failed`・`unresolved`・
  `child_processes`・`untraced_child_processes`・`startup_loaded`・`blocked_entry_open`・`excluded_from_import`・`entry_conditions`（verify）。
  既存のキー（`stubbable` / `nofollow` / `required` / `submodule_stub_hints`）の名前は変えていない
- 適用・復元: 元ファイルは項目ごとの `_pystub_bak/<項目名>/` へ移す（重ねた適用でも元ファイルを失わない）

### 0.2.0

- C 拡張パッケージの間接排除（サブモジュールのスタブ）
