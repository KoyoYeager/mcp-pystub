# 実証データとパフォーマンス / Verified results and performance

[README に戻る](../README.ja.md) | 測定条件、exe サイズ、解析時間・メモリ。 / Measurement conditions, exe sizes, analysis time and memory.

## 実証データ / Verified Results

測定条件: Windows 11、Python 3.13、PyInstaller（onefile）、各ライブラリを使う小さな `entry.py` を 1 本ずつ用意したシナリオ。
exe の出力が元と一致することと、site-packages の復元が元と一致することを確かめた値です（実装者とは別の検証者が再測定して再現）。

| シナリオ | スタブなし | 適用後 | 適用した候補 |
|---|---|---|---|
| 12_asammdf（Claude Code に頼んだ記録。README の表とデモ GIF の値） | 252.0 MB | **38.9 MB** | Claude が選んだ `asammdf.gui` だけ。出力 `10 18.0 V` は元と同じ |
| 12_asammdf | 240.1 MB | **36.9 MB** | 確認済み（実行）の `asammdf.gui`（PySide6・lxml を排除）だけ |
| 12_asammdf | 240.1 MB | **25.1 MB** | 静的解析の候補を全部（pandas + asammdf.gui） |
| 03_networkx | 76.7 MB | **40.8 MB** | scipy（記録からの候補）・fontTools・pandas |
| 01_tqdm | 47.7 MB | **33.3 MB** | pandas・fontTools・PIL（記録からの候補）。0.3.0 で stubbable の pandas も止まるスタブにしたため、以前の 32.7 MB から +0.6 MB（スタブの各モジュールに止まる仕組みの本体と、クラスの代わりの名前の一覧が入るため） |
| 08_matplotlib | 44.6 MB | **36.0 MB** | pandas・PIL.ImageQt |
| 02_openpyxl | 26.2 MB | **20.3 MB** | openpyxl.drawing.image（PIL を排除） |
| 15_pillow | 25.7 MB | **14.4 MB** | numpy |

## パフォーマンス / Performance

検証用の 15 シナリオで、analyze は 1 シナリオ 0.7〜23 秒（0.2.0 の約 2 倍）、
ピークメモリは最大 401 MB（scikit-learn のシナリオ。ほかは 400 MB 以下）。実行の記録の負荷は、記録しない実行の 1.3〜3.3 倍（10 シナリオ。実行が 0.1 秒程度の短い例ほど倍率が大きい）。
