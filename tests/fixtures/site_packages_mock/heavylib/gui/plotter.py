"""テスト用: GUI プロッター（qtlib を使用）"""

from qtlib import QApplication
from qtlib.QtCore import QObject


def plot(data):
    """グラフを描画する（GUI が必要）"""
    app = QApplication()
    return f"plotted {data}"
