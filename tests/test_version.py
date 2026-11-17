"""版の同期: mcp_pystub.__version__・pyproject.toml・server.json・MCP の serverInfo が同じ。"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_versions_match():
    import mcp_pystub
    py = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    sj = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert mcp_pystub.__version__ == py == sj["version"] == sj["packages"][0]["version"]


def test_server_info_version():
    import mcp_pystub
    from mcp_pystub.server import mcp
    assert mcp._mcp_server.version == mcp_pystub.__version__
