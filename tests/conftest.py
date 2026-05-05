"""Pytest config for tests/ subfolder — ensures project root is importable."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock streamlit session_state to avoid import-time failures in streamlit_app.py
import streamlit as st
session_state_mock = MagicMock()
session_state_mock.get = MagicMock(return_value=0)
session_state_mock.pop = MagicMock(return_value=False)
session_state_mock.__getitem__ = MagicMock()
session_state_mock.__setitem__ = MagicMock()
st.session_state = session_state_mock
