# 安全性と制限 / Safety and limitations

[README に戻る](../README.ja.md) | 保証の範囲、普段の実行との違い、制限の一覧、安全性の方針。 / Scope of the guarantee, differences from a normal run, limitations, and the safety policy.

## できないこと・制限事項 / Limitations

- **C 拡張パッケージ**（.pyd / .so）は直接スタブ化すると C 拡張が欠落するため required 判定。ただし `generate_submodule` で**間接排除が可能**
- **動的 import**（`importlib.import_module(変数)` / `__import__(変数)`）は静的解析で追跡不可。プロジェクトのコードに
  あれば安全側（required）、依存ライブラリの中なら warnings にモジュールと行を出し、判定の前提とします（仕様 5 節）
- **PyInstaller カスタムフック**: スタブと衝突する場合がありフックの手動無効化が必要
- **遅延初期化パターン**: `__init__` で直接呼ばず後のメソッドで使うパッケージは検出精度が下がる（安全側で required に判定）
- **pip 名と import 名の不一致**: `python-dateutil` → `dateutil` 等のマッピングは未対応
- **ランタイムの条件分岐**: `if sys.version_info < (3, 11)` 等の実行環境で決まる分岐は、両方の枝を「確実に実行される」
  側に倒します（安全側。削減を取りこぼすことがあります）

## 普段の実行との違い / Differences from a normal run

**普段の実行との違い**（verify のエントリー実行・import の確認・`python -m mcp_pystub.trace` の子プロセス）:

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

## 安全性の設計方針 / Safety Policy

使用が見つからなかった候補（stubbable / nofollow / submodule_stub_hints）・条件付きとも、**静的解析による提案**です
（[仕様](STUB_SAFETY_SPEC.md) 5 節の前提のもと）。**適用前に `verify(..., run_entry=True)` で、解析したエントリーが
変わらないことを確かめてください**。保証の最後の拠り所は verify と、ビルド後の実行結果の比較です。判定に迷う場合は
required（安全側）に倒し、逆方向の誤判定（本当は stubbable なのに required）は許容します。

条件付きは、条件を破ると原則として終了コード 70 で止まります。ただし、値の代わりに対する `isinstance` / `type()` /
`callable` / `dir()` / `vars()`、スタブのクラスの `issubclass` / `__mro__` / `dir()` / `vars()` / `dataclasses.is_dataclass`、外部の基底から
継承した属性では、止まる前に結果が元と変わりえます（Python の仕組み上スタブ側で止められない）。元に無い属性のエラー文も
元と違います。
