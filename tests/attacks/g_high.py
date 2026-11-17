"""high が実際に出る事例（第 6 回 基準 2）: プロジェクトは入口に届く公開関数を 1 つも呼ばない。
名前の振り分け（o.plot()）はライブラリの import 時のコードだけが行う。解析したエントリー（main.py）は変わらず、
条件を破る別の入口（alt.py）は終了コード 70 で止まる。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hc import cscenario, RESULTS  # noqa

WARM = {"warm/__init__.py": "def _d(objs):\n    return [o.plot() for o in objs]\nREADY = _d([])\n"}
MAIN = "import sig, warm\nprint('ok', sig.Signal(3).value(), warm.READY)\n"
ALT = "import sig\nprint('ok', sig.Signal(3).plot())\n"

# G01: 入口の中で重い関数を呼ぶ
cscenario("G01_high_call", {**WARM,
    "hv/__init__.py": "def render(v):\n    return 'R%s' % v\n",
    "sig/__init__.py": "class Signal:\n    def __init__(self, v): self.v = v\n    def value(self): return self.v\n"
                       "    def plot(self):\n        import hv\n        return hv.render(self.v)\n",
}, {"main.py": MAIN, "alt.py": ALT}, entries=("main.py", "alt.py"))

# G02: 入口の中でクラスの属性（元は値）を書式に使う
cscenario("G02_high_class_attr", {**WARM,
    "hv/__init__.py": "import os\nclass Renderer:\n    BACKEND = os.environ.get('HVB_X', 'agg')\n",
    "sig/__init__.py": "class Signal:\n    def __init__(self, v): self.v = v\n    def value(self): return self.v\n"
                       "    def plot(self):\n        import hv\n        return 'backend=%s' % (hv.Renderer.BACKEND,)\n",
}, {"main.py": MAIN, "alt.py": ALT}, entries=("main.py", "alt.py"))

# G03: 入口の中で Enum を文字列にする
cscenario("G03_high_enum", {**WARM,
    "hv/__init__.py": "import enum\nclass Color(enum.Enum):\n    RED = 1\n",
    "sig/__init__.py": "class Signal:\n    def __init__(self, v): self.v = v\n    def value(self): return self.v\n"
                       "    def plot(self):\n        import hv\n        return 'kind=%s' % (hv.Color.RED,)\n",
}, {"main.py": MAIN, "alt.py": ALT}, entries=("main.py", "alt.py"))

for n, f in RESULTS:
    print("SUMMARY", n, f)
