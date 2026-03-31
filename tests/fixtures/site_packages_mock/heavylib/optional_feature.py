"""テスト用: pandas に依存するオプション機能"""

import pandas  # ← これが stub 候補


class ReportGenerator:
    def generate(self, data):
        return pandas.DataFrame(data)
