# 開発 / Development

[README に戻る](../README.ja.md) | テストと、README の GIF の作り方。 / Tests and how the README GIF is made.

## テスト / Testing

```bash
python -m pytest tests/ -v
```

### テスト実績

| テスト種別 | 結果 |
|-----------|------|
| ユニットテスト（tests/、攻撃シナリオの回帰を含む） | 全通過（件数は[変更履歴](../CHANGELOG.md)に書く） |
| 実ビルド（[実証データ](results.md)の 6 本） | exe の出力一致・復元一致 |

README の冒頭の GIF は `tools/demo_gif.py` で作ります（本物の出力を保存してから描画します。使い方はファイルの先頭の説明）。
