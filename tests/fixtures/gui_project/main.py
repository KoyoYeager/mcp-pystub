"""テスト用: heavylib の core 機能のみ使用（GUI は使わない）

heavylib を直接 import するため __init__.py が実行され、
heavylib.gui → qtlib (C拡張) が transitive に import される。
しかしプロジェクトは plot() を呼ばない → heavylib.gui はスタブ化可能。

これは Mdf2CsvConverter で from asammdf import MDF とするパターンと同等。
"""

from heavylib import CoreProcessor


def main():
    proc = CoreProcessor()
    result = proc.run("data.mdf")
    print(result)


if __name__ == "__main__":
    main()
