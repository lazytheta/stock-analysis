"""Pytest config for tests/ subfolder — ensures project root is importable
and pre-imports streamlit_app so its module-level references to error_logger
and streamlit are bound to the real modules. This matters because root-level
tests (test_ibkr_api.py) mutate sys.modules at module-load time, and if
streamlit_app is imported AFTER that pollution, it picks up the mocks.
By pre-importing here we lock streamlit_app's references first."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock streamlit session_state BEFORE importing streamlit_app, so its
# module-level code that touches st.session_state["..."] doesn't crash.
import streamlit as st
session_state_mock = MagicMock()
session_state_mock.get = MagicMock(return_value=0)
session_state_mock.pop = MagicMock(return_value=False)
session_state_mock.__getitem__ = MagicMock()
session_state_mock.__setitem__ = MagicMock()
st.session_state = session_state_mock

# Pre-import streamlit_app so its `from error_logger import …` binding
# captures the real error_logger functions before any other test pollutes
# sys.modules. Best-effort: if import fails for any reason (e.g. CI without
# certain deps), skip silently — the tests that need streamlit_app will
# fail loudly with their own ImportError.
try:
    import streamlit_app  # noqa: F401
except Exception:
    pass
