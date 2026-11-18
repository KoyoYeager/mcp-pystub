# mcp-pystub

[English](https://github.com/KoyoYeager/mcp-pystub/blob/master/README.md) | 日本語

**依存ライブラリが読み込むだけで、あなたのコードからは一度も使われない重いパッケージを、exe から外す MCP サーバー。**

例えば asammdf で MDF ファイルを読み書きするだけのアプリでも、asammdf の GUI 部分が PySide6（Qt）を読み込むため、PyInstaller の exe は 240 MB になります。mcp-pystub は依存ライブラリの中まで関数単位で使い方を追い、外せる部分を最小のスタブに差し替え、実際に動かして出力が変わらないことを確かめます。差し替えは元に戻せます。

![本物の Claude Code の会話を記録した、asammdf のシナリオのデモ: mcp-pystub を一度登録して Claude に頼むと、Claude が analyze と verify を動かし、スタブを適用して exe をビルドし(252.0 MB から 26.5 MB、出力は同じ)、venv を元に戻す。外した部分に触れるとスタブが理由を出して終了コード 70 で止まる](https://raw.githubusercontent.com/KoyoYeager/mcp-pystub/master/docs/images/demo.ja.gif)

| ライブラリ（使い方の例） | exe（そのまま） | exe（適用後） | 外したもの |
|---|---|---|---|
| asammdf（MDF の読み書き） | 240.1 MB | **36.9 MB** | asammdf.gui → PySide6 |
| networkx | 76.7 MB | **40.8 MB** | scipy・fontTools・pandas |
| tqdm | 47.7 MB | **33.3 MB** | pandas・fontTools・PIL |
| matplotlib | 44.6 MB | **36.0 MB** | pandas・PIL.ImageQt |
| openpyxl | 26.2 MB | **20.3 MB** | PIL |
| Pillow | 25.7 MB | **14.4 MB** | numpy |

PyInstaller（onefile）、Windows 11、Python 3.13。どれも exe の出力が元と一致することを確かめた値です（詳しい条件は[実証データ](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/results.md)）。

## 使い方

Claude Code に一度登録して、Claude に頼むだけです。流れ全体は、上の GIF のとおりです。

```bash
claude mcp add pystub -- uvx mcp-pystub
```

プロジェクトのフォルダで、こう頼みます。

> mcp-pystub で main.py の exe を小さくして。venv は .venv。動かして確かめていいよ。

あとは Claude が、analyze、verify、スタブの適用、ビルド、venv を元に戻すところまで進めます(GIF のとおり)。uv が無い場合や他のクライアント(Claude Desktop など)は、[ツールと設定](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/tools.md)を見てください。

## 何が違うのか

- **関数単位で追う**: 型注釈・普通のクラスへの isinstance・except にしか使わないパッケージや、呼ばない関数の中でしか使わないパッケージを見分けます。
- **動かして確かめる**: verify がスタブを差し込んでエントリーを実際に動かし、標準出力と終了コードが変わらないことを確かめます（エントリーを動かすのは明示的に指定した時だけ）。
- **間違えたら止まる**: 外した部分に触れると、黙って結果を変えずに、理由を表示して終了コード 70 で止まります。
- **元に戻せる**: 元のファイルはバックアップされ、復元のコマンドが出ます。

静的解析だけでは「どんなコードでも壊れない」とは言えないので、提案は候補として出し、最後は動かして確かめる設計です。保証の範囲と制限は[安全性と制限](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/safety.md)を見てください。

## ドキュメント

- [仕組みと判定](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/how-it-works.md)（判定の種類、confidence、3 層、実行の記録）
- [安全性と制限](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/safety.md)（保証の範囲、普段の実行との違いの表、制限の一覧、安全性の方針）
- [ツールと設定](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/tools.md)（インストール、MCP の設定例、各ツールの引数と出力例）
- [実証データとパフォーマンス](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/results.md)（測定条件、解析時間・メモリ）
- [判定の仕様](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/STUB_SAFETY_SPEC.md)
- [変更履歴](https://github.com/KoyoYeager/mcp-pystub/blob/master/CHANGELOG.md)
- [開発](https://github.com/KoyoYeager/mcp-pystub/blob/master/docs/development.md)（テスト、README の GIF の作り方）

## ライセンス / License

MIT
