"""Pytest config for tests/ subfolder — ensures project root is importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
