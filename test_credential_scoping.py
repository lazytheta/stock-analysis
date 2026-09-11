"""Credential reads must be scoped to one user.

The Streamlit app passes a user-scoped client and leans on RLS. The MCP server
runs with the service-role key, which bypasses RLS — so without an explicit
user_id these queries match every user's rows. load_config already takes one;
these did not.
"""

import unittest
from unittest.mock import MagicMock

import config_store


class _Table:
    """Records the .eq() filters a query applied before executing."""

    def __init__(self, rows):
        self._rows = rows
        self.filters = {}

    def select(self, *_a, **_kw):
        # Een verse builder per query, zoals de echte client. Zonder dit erven
        # alle queries op één client de filters van de vorige, en slaagt de
        # test ook als de tweede read de user_id vergeet -- precies het lek
        # waar dit bestand voor bestaat.
        self.filters = {}
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def maybe_single(self):
        return self

    def execute(self):
        matched = [
            r for r in self._rows
            if all(r.get(k) == v for k, v in self.filters.items())
        ]
        if len(matched) > 1:
            # What PostgREST does to a maybe_single() that matches many rows.
            raise RuntimeError("JSON object requested, multiple rows returned")
        return MagicMock(data=matched[0] if matched else None)


def _client(rows):
    c = MagicMock()
    table = _Table(rows)
    c.table.return_value = table
    c._table = table
    return c


_TWO_USERS = [
    {"user_id": "user-a", "service_name": "t212_api_key", "credential": "KEY-A"},
    {"user_id": "user-b", "service_name": "t212_api_key", "credential": "KEY-B"},
]


class TestLoadCredential(unittest.TestCase):
    def test_it_filters_on_the_user_when_one_is_given(self):
        c = _client(_TWO_USERS)
        self.assertEqual(
            config_store.load_credential(c, "t212_api_key", user_id="user-b"),
            "KEY-B")
        self.assertEqual(c._table.filters.get("user_id"), "user-b")

    def test_without_a_user_it_does_not_filter(self):
        """The Streamlit path passes a user-scoped client and relies on RLS;
        adding a filter there would be harmless but the signature has to stay
        backwards compatible."""
        c = _client([_TWO_USERS[0]])
        self.assertEqual(config_store.load_credential(c, "t212_api_key"), "KEY-A")
        self.assertNotIn("user_id", c._table.filters)

    def test_an_unscoped_read_across_two_users_returns_nothing_rather_than_one(self):
        """Handing back whichever row PostgREST happened to return would give
        one user another's broker key. The query raises; the wrapper must not
        turn that into a plausible-looking answer."""
        c = _client(_TWO_USERS)
        self.assertIsNone(config_store.load_credential(c, "t212_api_key"))


class TestLoadT212Credentials(unittest.TestCase):
    def test_it_passes_the_user_through(self):
        rows = [
            *_TWO_USERS,
            {"user_id": "user-a", "service_name": "t212_api_secret",
             "credential": "SEC-A"},
            {"user_id": "user-b", "service_name": "t212_api_secret",
             "credential": "SEC-B"},
        ]
        creds = config_store.load_t212_credentials(_client(rows), user_id="user-b")
        self.assertEqual(creds, {"t212_api_key": "KEY-B",
                                 "t212_api_secret": "SEC-B"})

    def test_a_user_with_only_half_the_pair_is_not_connected(self):
        """Basic auth needs both. Returning a half-filled dict would fail later
        with a confusing 401 instead of "not connected"."""
        rows = [{"user_id": "user-a", "service_name": "t212_api_key",
                 "credential": "KEY-A"}]
        self.assertIsNone(
            config_store.load_t212_credentials(_client(rows), user_id="user-a"))

    def test_a_user_with_no_credentials_at_all(self):
        self.assertIsNone(
            config_store.load_t212_credentials(_client(_TWO_USERS),
                                               user_id="user-c"))


if __name__ == "__main__":
    unittest.main()
