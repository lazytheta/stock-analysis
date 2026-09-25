"""The Cloud Run image only starts if the Dockerfile COPYs every repo-root
module mcp_server.py imports at top level. aspirant.py went missing here once
(2026-09) and the container would fail on import at startup — nothing catches
that except actually building the image, which CI doesn't do. This parses
mcp_server.py's own top-level imports instead of hardcoding the module list,
so a future new top-level import is checked the same way.
"""

import ast
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _top_level_imported_names(pyfile):
    """Module names from top-level `import X` / `from X import ...` in pyfile.

    Only statements at module scope count — an import inside a function
    (e.g. a lazy `from supabase import create_client`) isn't needed at
    container startup, only when that function actually runs.
    """
    with open(pyfile, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=pyfile)
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


def _dockerfile_copy_text(dockerfile_path):
    """Text of every COPY instruction (including backslash continuation
    lines), concatenated. A module name mentioned only in a comment or a
    RUN line must not count."""
    with open(dockerfile_path, encoding="utf-8") as f:
        lines = f.readlines()
    chunks = []
    in_copy = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("COPY"):
            in_copy = True
        if in_copy:
            chunks.append(line)
            if not stripped.endswith("\\"):
                in_copy = False
    return "".join(chunks)


def test_dockerfile_copies_every_root_module_mcp_server_imports_at_top_level():
    mcp_server_path = os.path.join(REPO_ROOT, "mcp_server.py")
    dockerfile_path = os.path.join(REPO_ROOT, "Dockerfile")

    imported = _top_level_imported_names(mcp_server_path)
    # Only the ones that are actually repo-root modules (not "json", "os",
    # third-party packages like "mcp", etc.)
    root_modules = sorted(
        name for name in imported
        if os.path.isfile(os.path.join(REPO_ROOT, f"{name}.py"))
    )
    assert root_modules, "sanity check: expected mcp_server.py to import some root-level modules"

    copy_text = _dockerfile_copy_text(dockerfile_path)
    missing = [f"{name}.py" for name in root_modules if f"{name}.py" not in copy_text]
    assert not missing, (
        f"Dockerfile COPY is missing module(s) that mcp_server.py imports at "
        f"top level: {missing} — the Cloud Run container would fail to start."
    )
