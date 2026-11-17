"""splice.py FILE START_MARKER END_MARKER REPLACEMENT_FILE — FILE の START..END（END は残す）を置換する。"""
import sys
from pathlib import Path

f, start, end, rep = sys.argv[1:5]
p = Path(f)
s = p.read_text(encoding="utf-8")
i = s.index(start)
j = s.index(end, i) if end != "EOF" else len(s)
s = s[:i] + Path(rep).read_text(encoding="utf-8") + s[j:]
p.write_text(s, encoding="utf-8")
print("spliced", i, j)
