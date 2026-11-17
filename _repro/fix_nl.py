"""テスト中の、改行文字になってしまった "\\n" リテラルを直す。"""
import re
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "tests" / "test_regressions.py"
s = p.read_text(encoding="utf-8")
# "<改行>".join  →  "\n".join
s = s.replace('"\n".join', '"\\n".join')
# 文字列リテラル内に入った改行（"import heavyy<改行>print('ok')<改行>" など）
s = re.sub(r'"import heavyy\nprint\(\'ok\'\)\n"', lambda m: '"import heavyy\\nprint(\'ok\')\\n"', s)
s = re.sub(r'"import heavyx\nprint\(\'ok\', heavyx.MAX_SIZE\)\n"',
           lambda m: '"import heavyx\\nprint(\'ok\', heavyx.MAX_SIZE)\\n"', s)
s = re.sub(r'"import heavyx\n"\}\)\n        bi = generate_stubs',
           lambda m: '"import heavyx\\n"})\n        bi = generate_stubs', s)
p.write_text(s, encoding="utf-8")
print("ok")
