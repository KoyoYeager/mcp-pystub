"""テスト用: try/except 保護された import"""

try:
    import optional_package
except ImportError:
    optional_package = None

import heavylib
