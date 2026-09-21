"""Tests for the remember-me flow.

The bug these exist for: Supabase rotates refresh tokens. Every refresh
consumes the token it was handed and issues a new one, and the consumed one
stops working within seconds. handle_remember_me refreshed and then threw the
replacement away, so the second visit presented a spent token and was refused
— logging the user out roughly every other time they opened the app.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _fake_st():
    """A stand-in for streamlit with the two surfaces auth.py touches."""
    st = types.SimpleNamespace()
    st.html_calls = []
    st.html = lambda body, **kw: st.html_calls.append(body)
    st.context = types.SimpleNamespace(cookies={})
    st.session_state = {}
    st.query_params = {}
    return st


def _client_with_tokens(*tokens):
    """A Supabase client whose session hands back `tokens` in order.

    One entry per get_session() call, so a test can watch the token change
    underneath a refresh the way the real rotation does.
    """
    client = MagicMock()
    seq = list(tokens)

    def _get_session():
        s = MagicMock()
        s.refresh_token = seq.pop(0) if len(seq) > 1 else seq[0]
        return s

    client.auth.get_session.side_effect = _get_session
    client.auth.get_user.return_value.user = types.SimpleNamespace(
        id="u-1", email="a@b.nl")
    return client


class TestRememberMe(unittest.TestCase):
    def setUp(self):
        import auth
        self.auth = auth
        self.st = _fake_st()
        self._patch = patch.object(auth, "st", self.st)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _store_writes(self):
        """The token values this run wrote to localStorage, in order."""
        out = []
        for body in self.st.html_calls:
            if "localStorage.setItem" in body:
                out.append(body.split("localStorage.setItem(")[1]
                           .split(",", 1)[1].split(")")[0].strip().strip('"'))
        return out

    def _store_clears(self):
        return [b for b in self.st.html_calls if "localStorage.removeItem" in b]

    def test_a_rotated_token_is_written_back(self):
        # The heart of it: R1 goes in, Supabase issues R2, R2 must be stored.
        client = _client_with_tokens("R2")
        with patch.object(self.auth, "init_auth_client", return_value=client):
            got_client, user = self.auth.handle_remember_me("R1")
        assert got_client is client and user.email == "a@b.nl"
        client.auth.refresh_session.assert_called_once_with("R1")
        assert self._store_writes() == ["R2"]

    def test_nothing_stored_means_no_attempt(self):
        with patch.object(self.auth, "init_auth_client") as mk:
            assert self.auth.handle_remember_me("") == (None, None)
        mk.assert_not_called()

    def test_a_dead_token_is_cleared_rather_than_retried_forever(self):
        client = MagicMock()
        client.auth.refresh_session.side_effect = RuntimeError("Invalid Refresh Token: spent")
        with patch.object(self.auth, "init_auth_client", return_value=client):
            assert self.auth.handle_remember_me("spent") == (None, None)
        assert self._store_clears()

    def test_the_token_never_reaches_the_logs(self):
        # Supabase puts the token in the exception text, and that text used to
        # be written into the error_logs table verbatim.
        client = MagicMock()
        client.auth.refresh_session.side_effect = RuntimeError(
            "Invalid Refresh Token: s3cret-token")
        with patch.object(self.auth, "init_auth_client", return_value=client), \
             patch.object(self.auth, "logger") as log:
            self.auth.handle_remember_me("s3cret-token")
        for call in log.warning.call_args_list:
            assert "s3cret-token" not in str(call), call

    def test_the_token_never_reaches_the_url(self):
        # It used to be handed to Python through a query parameter, which put
        # a long-lived credential in the address bar and the browser history.
        client = _client_with_tokens("R2")
        with patch.object(self.auth, "init_auth_client", return_value=client):
            self.auth.handle_remember_me("R1")
        assert self.st.query_params == {}
        assert not any("searchParams" in b for b in self.st.html_calls)

    def test_the_old_cookie_is_dropped_on_every_write(self):
        # Browsers that logged in under the cookie route still carry one.
        client = _client_with_tokens("R2")
        self.auth.save_session_to_browser(client)
        assert "Max-Age=0" in self.st.html_calls[-1]

    def test_a_failure_to_persist_does_not_take_the_login_down(self):
        client = MagicMock()
        client.auth.get_session.side_effect = RuntimeError("network")
        self.auth.save_session_to_browser(client)  # must not raise

    def test_logging_out_clears_storage_and_the_old_cookie(self):
        self.auth.clear_browser_session()
        joined = "".join(self.st.html_calls)
        assert "Max-Age=0" in joined
        assert "localStorage.removeItem" in joined


class TestRestoreIntoState(unittest.TestCase):
    """De token komt via een component uit de browser, niet via een cookie:
    Streamlit Community Cloud filtert cookies weg in zijn proxy, dus
    st.context.cookies was daar altijd leeg en elke refresh begon bij de
    login -- terwijl het lokaal, en dus in de tests, gewoon werkte."""

    def setUp(self):
        import auth
        self.auth = auth
        self.st = _fake_st()
        self.st.rerun = MagicMock(side_effect=AssertionError(
            "een rerun hier laat het opslag-script vallen"))
        self._patch = patch.object(auth, "st", self.st)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_a_restore_fills_the_session_without_rerunning(self):
        client = _client_with_tokens("R2")
        with patch.object(self.auth, "read_browser_token", return_value="R1"), \
             patch.object(self.auth, "init_auth_client", return_value=client):
            ok = self.auth.restore_session_into_state()
        assert ok is True
        assert self.st.session_state["supabase_client"] is client
        assert self.st.session_state["user"] == {"id": "u-1", "email": "a@b.nl"}
        self.st.rerun.assert_not_called()
        assert any("R2" in b for b in self.st.html_calls), \
            "de geroteerde token moet in deze run geschreven zijn"

    def test_nothing_stored_is_a_plain_no(self):
        with patch.object(self.auth, "read_browser_token", return_value=""):
            assert self.auth.restore_session_into_state() is False
        assert "supabase_client" not in self.st.session_state

    def test_an_unanswered_browser_is_not_a_no(self):
        """None is 'nog niet': de aanroeper wacht op de volgende run in
        plaats van de login te tonen aan iemand die zo ingelogd blijkt."""
        with patch.object(self.auth, "read_browser_token", return_value=None), \
             patch.object(self.auth, "init_auth_client") as mk:
            assert self.auth.restore_session_into_state() is None
        mk.assert_not_called()


if __name__ == "__main__":
    sys.exit(unittest.main())
