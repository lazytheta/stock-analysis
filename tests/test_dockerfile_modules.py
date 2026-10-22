"""Every repo module the MCP server can import must be copied into its image.

The Dockerfile lists files by hand. t212_positions failed on Cloud Run with
"No module named 'portfolio_metrics'" (found 2026-10-22) because t212_api
gained imports that never reached that list. This walks the imports from the
server's entry points, including those inside functions, and checks each
local module against the COPY lines.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Imported only inside a function, behind a fallback, and needing packages
# the image does not install (streamlit, the tastytrade SDK). Cloud Run takes
# quotes from Nasdaq instead (quotes.py).
NOT_IN_IMAGE = {"tastytrade_api", "error_logger"}


def _imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def test_every_reachable_module_is_copied_into_the_image():
    local = {p.stem for p in ROOT.glob("*.py")}
    copied = set(re.findall(r"([A-Za-z_0-9]+)\.py", (ROOT / "Dockerfile").read_text()))
    todo = set()
    for entry in ("main.py", "mcp_handler.py"):
        todo |= _imports(ROOT / "lazytheta-mcp-cloudrun" / entry)
    seen = set()
    while todo:
        mod = todo.pop()
        if mod in seen or mod not in local or mod in NOT_IN_IMAGE:
            continue
        seen.add(mod)
        todo |= _imports(ROOT / f"{mod}.py")
    assert sorted(seen - copied) == []
