"""Tests voor de notities-MCP. Draaien zonder Supabase: de opslagclient
wordt geïnjecteerd, net zoals compute_screener(universe, fetch) dat doet."""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))

USER = "02d70077-9c3f-4494-a37d-0fa3e8f68f98"


# ── vault_paths ────────────────────────────────────────────────────────────

def test_storage_key_prefixes_the_user():
    from vault_paths import storage_key
    assert storage_key(USER, "portfolio-vault", "Tickers/DECK.md") == (
        f"{USER}/portfolio-vault/Tickers/DECK.md")


def test_parent_traversal_is_refused():
    """Een pad dat uit de vault klimt komt bij de notities van iemand anders
    uit. Dit is de les uit het load_credential-lek: de user-id hoort niet
    beinvloedbaar te zijn door wat de aanroeper meestuurt."""
    from vault_paths import UnsafePath, storage_key
    for bad in ("../other/x.md", "Tickers/../../x.md", "..%2Fx.md"):
        with pytest.raises(UnsafePath):
            storage_key(USER, "portfolio-vault", bad)


def test_absolute_paths_are_refused():
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "portfolio-vault", "/etc/passwd.md")


def test_only_markdown_is_addressable():
    """De tools werken op notities. Bijlagen synchroniseren wel mee, maar
    zijn geen tekst die je kunt lezen of doorzoeken."""
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "portfolio-vault", "Attachments/plaatje.png")


def test_a_vault_name_cannot_contain_a_separator():
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "../andere-user", "x.md")


def test_vault_prefix_without_vault_is_the_user_root():
    from vault_paths import vault_prefix
    assert vault_prefix(USER) == f"{USER}/"
    assert vault_prefix(USER, "lazytheta-vault") == f"{USER}/lazytheta-vault/"


def test_note_path_is_the_inverse_of_storage_key():
    from vault_paths import note_path, storage_key
    key = storage_key(USER, "lazytheta-vault", "06 Open Issues.md")
    assert note_path(USER, "lazytheta-vault", key) == "06 Open Issues.md"


def test_double_url_encoding_is_refused():
    """Double-encoding (e.g., %25 → %) can bypass single-pass decoding.
    A path that looks safe after one decode might be unsafe after two."""
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "portfolio-vault", "..%252Fx.md")


def test_deep_encoding_is_refused_instead_of_falling_open():
    """Vanaf vijf lagen stopte de decodeerlus en werd de half-gedecodeerde
    vorm zowel gevalideerd als gebruikt -- de sleutel bevatte dan nog een
    gecodeerde padscheiding. Niet stabiel binnen de limiet is geen reden om
    door te gaan, maar om te weigeren."""
    from vault_paths import UnsafePath, storage_key
    for bad in ("..%252525252Fx.md", "..%25252525252Fx.md"):
        with pytest.raises(UnsafePath):
            storage_key(USER, "v", bad)


def test_a_percent_in_a_real_filename_is_preserved():
    """De sleutel wordt uit de oorspronkelijke invoer gebouwd, niet uit de
    gedecodeerde vorm. Anders is een notitie die werkelijk '%20' of '%2B' in
    haar naam heeft onbereikbaar, met NoteNotFound als misleidende melding."""
    from vault_paths import storage_key
    assert storage_key(USER, "v", "Q1 %20 review.md") == f"{USER}/v/Q1 %20 review.md"
    assert storage_key(USER, "v", "Rendement 100%2B.md") == f"{USER}/v/Rendement 100%2B.md"


def test_vault_name_is_decoded_and_validated():
    """The vault name must also be decoded and checked for traversal.
    ..%2Fandere-user decodes to ../andere-user and should be refused."""
    from vault_paths import UnsafePath, storage_key
    with pytest.raises(UnsafePath):
        storage_key(USER, "..%2Fandere-user", "x.md")


def test_single_dot_segments_are_filtered():
    """A path like ./x.md should be equivalent to x.md.
    Both refer to the same note; single-dot segments should be normalized away."""
    from vault_paths import storage_key
    path_with_dot = storage_key(USER, "vault", "./x.md")
    path_without_dot = storage_key(USER, "vault", "x.md")
    assert path_with_dot == path_without_dot


# ── vault_storage ──────────────────────────────────────────────────────────

def _supabase_404():
    """De fout die Supabase Storage echt teruggeeft bij een ontbrekend object.

    Gemeten tegen de live endpoint op 2026-09-08: HTTP 404, en een LEGE
    Error.Code en Error.Message. Geen NoSuchKey. De oude dubbel verzon die
    code, waardoor de tests groen bleven terwijl read_note in productie
    "Vault storage unavailable" meldde op elk verkeerd pad.
    """
    from botocore.exceptions import ClientError
    return ClientError(
        {"Error": {"Code": "", "Message": ""},
         "ResponseMetadata": {"HTTPStatusCode": 404}}, "GetObject")


class FakeS3:
    """Genoeg van de boto3-client om VaultStore te testen. Elke sleutel
    bevat (inhoud, etag); een sleutel in `broken` gooit een transportfout."""

    def __init__(self, objects=None, broken=()):
        self.objects = objects or {}
        self.broken = set(broken)
        self.calls = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        outer = self

        class _Pager:
            def paginate(self, Bucket, Prefix):
                if "LIST" in outer.broken:
                    raise OSError("bucket unreachable")
                keys = sorted(k for k in outer.objects if k.startswith(Prefix))
                # twee pagina's, zodat paginering echt getest wordt
                half = max(1, len(keys) // 2)
                for chunk in (keys[:half], keys[half:]):
                    yield {"Contents": [{"Key": k} for k in chunk]} if chunk else {}

        return _Pager()

    def list_objects_v2(self, Bucket, MaxKeys=1):
        if "LIST" in self.broken:
            raise OSError("bucket unreachable")
        return {"KeyCount": 0}

    def put_object(self, Bucket, Key, Body, **kw):
        if "PUT" in self.broken:
            raise OSError("write refused")
        text = Body.decode("utf-8") if isinstance(Body, bytes) else Body
        etag = f'"w{len(self.objects)}"'
        self.objects[Key] = (text, etag)
        return {"ETag": etag}

    def get_object(self, Bucket, Key):
        self.calls.append(Key)
        if Key in self.broken:
            raise OSError("connection reset")
        if Key not in self.objects:
            raise _supabase_404()
        body, etag = self.objects[Key]

        class _Body:
            def read(self_inner):
                return body.encode("utf-8")

        return {"Body": _Body(), "ETag": etag}


def test_list_keys_walks_every_page():
    """Eén pagina lezen en stoppen laat notities onzichtbaar achter -- het
    soort stille verlies waar de floor-guard in refresh_universe voor is."""
    from vault_storage import VaultStore
    objects = {f"{USER}/v/n{i}.md": (f"tekst {i}", f'"e{i}"') for i in range(9)}
    store = VaultStore(FakeS3(objects), "vaults")
    assert len(store.list_keys(f"{USER}/v/")) == 9


def test_get_returns_text_and_revision():
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("hallo", '"abc123"')}), "vaults")
    text, revision = store.get(f"{USER}/v/a.md")
    assert text == "hallo"
    assert revision == "abc123"          # zonder de aanhalingstekens van S3


def test_a_missing_note_is_not_an_empty_note():
    from vault_storage import NoteNotFound, VaultStore
    store = VaultStore(FakeS3({}), "vaults")
    with pytest.raises(NoteNotFound):
        store.get(f"{USER}/v/weg.md")


def test_a_transport_failure_never_looks_like_data():
    """Een lege lijst teruggeven terwijl de bucket plat ligt is een onware
    uitspraak over de vault. Zelfde regel als EdgarFetchError."""
    from vault_storage import StorageUnavailable, VaultStore
    store = VaultStore(FakeS3({}, broken=["LIST"]), "vaults")
    with pytest.raises(StorageUnavailable):
        store.list_keys(f"{USER}/")


def test_get_raises_when_the_connection_breaks():
    from vault_storage import StorageUnavailable, VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("x", '"e"')}, broken=[f"{USER}/v/a.md"]),
                       "vaults")
    with pytest.raises(StorageUnavailable):
        store.get(f"{USER}/v/a.md")


class _RaisingClient:
    """Een client die bij get_object altijd dezelfde exceptie werpt."""

    def __init__(self, exc):
        self._exc = exc

    def get_object(self, Bucket, Key):
        raise self._exc


def _client_error(code):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": code}}, "GetObject")


def test_a_missing_bucket_is_not_a_missing_note():
    """NoSuchBucket telde als 'ontbrekend' en werd dus NoteNotFound: "die
    notitie is er niet", terwijl de hele bucket weg of verkeerd is. Precies de
    storing-als-data die dit project verbiedt."""
    from vault_storage import StorageUnavailable, VaultStore
    store = VaultStore(_RaisingClient(_client_error("NoSuchBucket")), "vaults")
    with pytest.raises(StorageUnavailable):
        store.get(f"{USER}/v/a.md")


def test_an_exception_without_a_response_does_not_crash():
    """Een exceptie met .response is None liet _is_missing met AttributeError
    klappen, en die crash ontsnapte aan de except van get()."""
    from vault_storage import StorageUnavailable, VaultStore

    class _Weird(Exception):
        response = None

    store = VaultStore(_RaisingClient(_Weird("kapot")), "vaults")
    with pytest.raises(StorageUnavailable):
        store.get(f"{USER}/v/a.md")


def test_nosuchkey_still_counts_as_missing():
    from vault_storage import NoteNotFound, VaultStore
    store = VaultStore(_RaisingClient(_client_error("NoSuchKey")), "vaults")
    with pytest.raises(NoteNotFound):
        store.get(f"{USER}/v/a.md")


def test_get_many_returns_every_requested_note():
    from vault_storage import VaultStore
    objects = {f"{USER}/v/n{i}.md": (f"tekst {i}", f'"e{i}"') for i in range(20)}
    store = VaultStore(FakeS3(objects), "vaults")
    got = store.get_many(list(objects))
    assert len(got) == 20
    assert got[f"{USER}/v/n7.md"] == "tekst 7"


# ── notes_tools ────────────────────────────────────────────────────────────

def _store_with_vaults():
    from vault_storage import VaultStore
    objects = {
        f"{USER}/portfolio-vault/CLAUDE.md": ("# regels\nfrontmatter verplicht", '"c1"'),
        f"{USER}/portfolio-vault/Tickers/DECK.md": (
            "---\nticker: DECK\n---\nDe wheel op DECK liep goed dit jaar.", '"d1"'),
        f"{USER}/portfolio-vault/Tickers/MSFT.md": ("MSFT is een compounder.", '"m1"'),
        f"{USER}/lazytheta-vault/06 Open Issues.md": ("Kapotte fair values.", '"o1"'),
        f"{USER}/portfolio-vault/Attachments/plaatje.png": ("binair", '"p1"'),
    }
    return VaultStore(FakeS3(objects), "vaults")


def test_list_vaults_counts_notes_and_finds_the_rules():
    """De CLAUDE.md van een vault beschrijft hoe erin geschreven hoort te
    worden. Die moet vindbaar zijn voordat fase 2 iets wegschrijft."""
    from notes_tools import list_vaults
    got = {v["vault"]: v for v in list_vaults(_store_with_vaults(), USER)}
    assert set(got) == {"portfolio-vault", "lazytheta-vault"}
    assert got["portfolio-vault"]["notes"] == 3        # de png telt niet mee
    assert got["portfolio-vault"]["claude_md"] == "CLAUDE.md"
    assert got["lazytheta-vault"]["claude_md"] is None


def test_read_note_returns_content_and_revision():
    from notes_tools import read_note
    got = read_note(_store_with_vaults(), USER, "portfolio-vault", "Tickers/DECK.md")
    assert "wheel op DECK" in got["content"]
    assert got["revision"] == "d1"
    assert got["path"] == "Tickers/DECK.md"


def test_read_note_refuses_to_leave_the_vault():
    from notes_tools import read_note
    from vault_paths import UnsafePath
    with pytest.raises(UnsafePath):
        read_note(_store_with_vaults(), USER, "portfolio-vault", "../lazytheta-vault/x.md")


def test_search_finds_the_note_and_shows_why():
    from notes_tools import search_notes
    got = search_notes(_store_with_vaults(), USER, "wheel")
    hits = got["hits"]
    assert len(hits) == 1
    assert hits[0]["path"] == "Tickers/DECK.md"
    assert hits[0]["vault"] == "portfolio-vault"
    assert "wheel" in hits[0]["snippet"].lower()


def test_search_is_case_insensitive():
    from notes_tools import search_notes
    assert search_notes(_store_with_vaults(), USER, "COMPOUNDER")["hits"]


def test_search_can_be_scoped_to_one_vault():
    """Alles doorzoeken kost bij portfolio-vault 177 objecten; op naam
    beperken scheelt tijd en egress."""
    from notes_tools import search_notes
    assert search_notes(_store_with_vaults(), USER, "fair values",
                        vault="portfolio-vault")["hits"] == []
    assert search_notes(_store_with_vaults(), USER, "fair values",
                        vault="lazytheta-vault")["hits"]


def test_search_skips_attachments():
    from notes_tools import search_notes
    assert search_notes(_store_with_vaults(), USER, "binair")["hits"] == []


def test_snippet_shows_context_around_the_match():
    from notes_tools import snippet
    text = "a" * 300 + "NAALD" + "b" * 300
    got = snippet(text, "naald", radius=20)
    assert "NAALD" in got
    assert len(got) < 100
    assert got.startswith("…") and got.endswith("…")


def test_snippet_without_a_match_returns_the_opening():
    from notes_tools import snippet
    assert snippet("korte notitie", "bestaat niet").startswith("korte")


def test_list_vaults_detects_unfiled_notes():
    """Notities zonder vaultmap (los onder de user-prefix) moeten zichtbaar
    zijn; anders kan de gebruiker niet zien of configuratie fout is."""
    from vault_storage import VaultStore
    objects = {
        f"{USER}/los.md": ("losse notitie", '"l1"'),
        f"{USER}/vault1/x.md": ("in vault", '"v1"'),
    }
    from notes_tools import list_vaults
    got = {v["vault"]: v for v in list_vaults(VaultStore(FakeS3(objects), "vaults"), USER)}
    assert None in got                       # geen sentinel-string: null
    assert got[None]["notes"] == 1
    assert got[None]["claude_md"] is None


def test_search_in_unfiled_notes():
    """Losse notities moeten ook doorzoekbaar zijn."""
    from vault_storage import VaultStore
    objects = {
        f"{USER}/los.md": ("losse notitie met zoekvraag", '"l1"'),
    }
    from notes_tools import search_notes
    hits = search_notes(VaultStore(FakeS3(objects), "vaults"), USER, "zoekvraag")["hits"]
    assert len(hits) == 1
    assert hits[0]["vault"] is None
    assert hits[0]["path"] == "los.md"


def test_list_vaults_shows_attachments_only_vault_with_zero_notes():
    """Een vault met alleen bijlagen (geen .md) moet wel verschijnen met notes: 0,
    niet verdwijnen. Anders is dat dezelfde blinde vlek als losse notities."""
    from vault_storage import VaultStore
    objects = {
        f"{USER}/attachments-only/image.png": ("binair", '"i1"'),
        f"{USER}/attachments-only/video.mp4": ("binair", '"v1"'),
    }
    from notes_tools import list_vaults
    got = {v["vault"]: v for v in list_vaults(VaultStore(FakeS3(objects), "vaults"), USER)}
    assert "attachments-only" in got
    assert got["attachments-only"]["notes"] == 0
    assert got["attachments-only"]["claude_md"] is None


def test_search_skips_unfiled_attachments():
    """Bijlagen buiten elke vault moeten niet doorzoekbaar zijn."""
    from vault_storage import VaultStore
    objects = {
        f"{USER}/plaatje.png": ("binair data met zoekvraag", '"p1"'),
    }
    from notes_tools import search_notes
    assert search_notes(VaultStore(FakeS3(objects), "vaults"), USER,
                        "zoekvraag")["hits"] == []


def test_a_real_unfiled_vault_no_longer_collides_with_loose_notes():
    """Bestaat er echt een vault genaamd (unfiled), dan is die nu ondubbelzinnig
    te onderscheiden van de losse notities: de echte vault houdt haar naam, de
    losse notities krijgen null. Twee vermeldingen met dezelfde naam bestaan
    niet meer."""
    from vault_storage import VaultStore
    objects = {
        f"{USER}/(unfiled)/real.md": ("in de echte vault", '"r1"'),
        f"{USER}/(unfiled)/CLAUDE.md": ("regels voor deze vault", '"c1"'),
        f"{USER}/loose.md": ("losse notitie", '"l1"'),
    }
    from notes_tools import list_vaults
    vaults = list_vaults(VaultStore(FakeS3(objects), "vaults"), USER)
    by_name = {v["vault"]: v for v in vaults}
    assert len(by_name) == len(vaults) == 2      # geen dubbele naam meer
    assert by_name["(unfiled)"]["notes"] == 2
    assert by_name["(unfiled)"]["claude_md"] == "CLAUDE.md"
    assert by_name[None]["notes"] == 1
    assert by_name[None]["claude_md"] is None


def test_an_uninterpretable_key_is_skipped_and_not_fatal():
    """Eén rare sleutel in de bucket legde list_vaults plat met UnsafePath: de
    hele tool gaf een fout in plaats van de overige vaults. Een sleutel die we
    niet kunnen duiden hoort overgeslagen te worden."""
    from notes_tools import list_vaults, search_notes
    from vault_storage import VaultStore
    objects = {
        f"{USER}/../x.md": ("rare sleutel met naald", '"x1"'),
        f"{USER}/./y.md": ("nog een rare, met naald", '"y1"'),
        f"{USER}/echte-vault/n.md": ("gewone notitie met naald", '"n1"'),
        f"{USER}/los.md": ("losse notitie met naald", '"l1"'),
    }
    store = VaultStore(FakeS3(objects), "vaults")

    vaults = list_vaults(store, USER)
    assert [v["vault"] for v in vaults] == ["echte-vault", None]
    assert vaults[0]["notes"] == 1
    assert vaults[-1]["notes"] == 1          # alleen los.md, niet de rare sleutels

    hits = search_notes(store, USER, "naald")["hits"]
    assert sorted(h["path"] for h in hits) == ["los.md", "n.md"]


def test_loose_notes_appear_last_in_list():
    """De null-vermelding moet achteraan staan, niet vooraan. Dat betekent dat
    losse notities pas na de echte vaults zichtbaar zijn."""
    from vault_storage import VaultStore
    objects = {
        f"{USER}/alice/x.md": ("", '"a1"'),
        f"{USER}/bob/y.md": ("", '"b1"'),
        f"{USER}/charlie/z.md": ("", '"c1"'),
        f"{USER}/loose.md": ("", '"l1"'),
    }
    from notes_tools import list_vaults
    vaults = list_vaults(VaultStore(FakeS3(objects), "vaults"), USER)
    assert vaults[-1]["vault"] is None
    assert [v["vault"] for v in vaults] == ["alice", "bob", "charlie", None]


def test_search_says_that_it_truncated_and_how_many_it_found():
    """"Heb ik ooit over X geschreven?" met 50 treffers en max_results=20 gaf
    20 resultaten zonder enige markering -- een onvolledig antwoord dat niet
    van een volledig te onderscheiden was."""
    from notes_tools import search_notes
    from vault_storage import VaultStore
    objects = {f"{USER}/v/n{i:03d}.md": ("naald in deze notitie", f'"e{i}"')
               for i in range(50)}
    got = search_notes(VaultStore(FakeS3(objects), "vaults"), USER, "naald",
                       max_results=20)
    assert len(got["hits"]) == 20
    assert got["returned"] == 20
    assert got["total_matches"] == 50
    assert got["truncated"] is True


def test_search_says_it_did_not_truncate_when_everything_fits():
    from notes_tools import search_notes
    got = search_notes(_store_with_vaults(), USER, "wheel")
    assert got["total_matches"] == 1
    assert got["returned"] == 1
    assert got["truncated"] is False


def test_every_search_hit_reads_back_as_the_same_note():
    """De kern van punt 5. Met de sentinel-string leverde een losse treffer
    ofwel NoteNotFound op (zoeken adverteerde onleesbare notities), ofwel --
    met een échte vault (unfiled) erbij -- stilletjes de inhoud van een andere
    notitie. Een treffer moet altijd terugleiden naar precies die notitie."""
    from notes_tools import read_note, search_notes
    from vault_storage import VaultStore
    objects = {
        f"{USER}/(unfiled)/los.md": ("in de echte vault, met naald", '"a1"'),
        f"{USER}/los.md": ("los onder de gebruiker, met naald", '"b1"'),
        f"{USER}/echte-vault/diep/los.md": ("diep in een vault, met naald", '"c1"'),
    }
    store = VaultStore(FakeS3(objects), "vaults")
    hits = search_notes(store, USER, "naald")["hits"]

    assert len(hits) == 3
    assert {h["vault"] for h in hits} == {"(unfiled)", None, "echte-vault"}
    for hit in hits:
        got = read_note(store, USER, hit["vault"], hit["path"])
        assert got["content"] == hit["snippet"]


def test_read_note_works_on_a_loose_note_with_vault_none():
    from notes_tools import read_note
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({f"{USER}/los.md": ("losse notitie", '"l1"')}), "vaults")
    got = read_note(store, USER, None, "los.md")
    assert got["content"] == "losse notitie"
    assert got["vault"] is None


def test_storage_key_without_a_vault_sits_under_the_user():
    from vault_paths import note_path, storage_key
    key = storage_key(USER, None, "los.md")
    assert key == f"{USER}/los.md"
    assert note_path(USER, None, key) == "los.md"


# ── MCP-laag ───────────────────────────────────────────────────────────────

import asyncio


@pytest.fixture(autouse=True)
def _set_jwt_key(monkeypatch):
    """mcp_auth.py vereist JWT_SIGNING_KEY zodra hij geimporteerd en gebruikt
    wordt (via main.py). Zelfde patroon als lazytheta-mcp-cloudrun/test_app.py."""
    monkeypatch.setenv("JWT_SIGNING_KEY", "test-key-not-for-production")


def test_the_four_tools_are_advertised():
    import mcp_handler
    assert {t["name"] for t in mcp_handler.TOOLS} == {
        "list_vaults", "search_notes", "read_note", "write_note"}


def test_write_is_the_only_mutation_offered():
    """Schrijven is er sinds fase 2; verwijderen, hernoemen en verplaatsen
    bewust niet. Een model dat een pad kan raden mag een notitie kunnen maken,
    niet er een laten verdwijnen."""
    import mcp_handler
    names = {t["name"] for t in mcp_handler.TOOLS}
    assert "write_note" in names
    assert not {n for n in names if n.startswith(("delete", "remove", "move", "rename"))}
    assert names == set(mcp_handler.TOOL_HANDLERS)


def test_every_tool_declares_a_schema():
    import mcp_handler
    for tool in mcp_handler.TOOLS:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


def test_a_call_without_a_user_is_refused():
    """user_id komt uit het JWT. Geen JWT, geen toegang tot notities."""
    import mcp_handler
    resp = asyncio.run(mcp_handler._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_vaults", "arguments": {}}}, None))
    assert resp["error"]["code"] == -32001


def test_the_user_id_in_the_arguments_is_ignored(monkeypatch):
    """Meesturen van een andere user_id mag niets doen -- de server neemt
    hem uit het token. Dit is het load_credential-lek in testvorm."""
    import mcp_handler
    seen = {}

    def fake_list_vaults(store, user_id):
        seen["user_id"] = user_id
        return []

    monkeypatch.setattr(mcp_handler, "_store", lambda: object())
    monkeypatch.setattr(mcp_handler.notes_tools, "list_vaults", fake_list_vaults)
    asyncio.run(mcp_handler._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "list_vaults",
                    "arguments": {"user_id": "iemand-anders"}}}, USER))
    assert seen["user_id"] == USER


def test_tools_list_needs_no_user():
    import mcp_handler
    resp = asyncio.run(mcp_handler._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, None))
    assert len(resp["result"]["tools"]) == 4


def test_health_names_this_service_and_not_the_other():
    """Deze test stond er als `assert app is not None` en slaagde daarmee ook
    toen `import main` LazyTheta's module opleverde. De servicenaam is het
    kleinste dat werkelijk deze app vastlegt."""
    from starlette.testclient import TestClient

    from main import app
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "notes-mcp"}


def test_mcp_without_a_token_is_401():
    from starlette.testclient import TestClient

    from main import app
    response = TestClient(app).post(
        "/mcp", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
    assert response.status_code == 401
    assert "resource_metadata" in response.headers.get("www-authenticate", "")


def test_both_services_run_the_same_auth_middleware():
    """SmartAuthMiddleware stond in tweevoud in de twee main.py's -- twee
    handgehouden kopieën van de code die bepaalt óf je binnenkomt en als wie.
    Nu één definitie in mcp_auth; hier vastgelegd zodat er niet stilletjes een
    tweede terugkomt."""
    import mcp_auth
    import main
    assert type(main.create_app()) is mcp_auth.SmartAuthMiddleware


# ── de kernregel: storing is geen data, en users zien elkaar niet ───────────

def _call_tool(monkeypatch, store, tool, args, user_id=USER):
    """Roep een tool aan door de hele MCP-laag heen, met een nep-bucket."""
    import mcp_handler
    monkeypatch.setattr(mcp_handler, "_store", lambda: store)
    return asyncio.run(mcp_handler._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool, "arguments": args}}, user_id))


def _call_json(monkeypatch, store, tool, args, user_id=USER):
    """Als _call_tool, maar geeft terug wat de client werkelijk te zien krijgt."""
    import json
    resp = _call_tool(monkeypatch, store, tool, args, user_id)
    assert "error" not in resp, resp
    assert not resp["result"].get("isError"), resp
    return json.loads(resp["result"]["content"][0]["text"])


def test_tools_report_an_unreachable_bucket_as_an_error(monkeypatch):
    """De -32002-tak, voor elke tool die de bucket oplijst. Een geslaagd
    resultaat met een lege lijst zou zeggen "je hebt geen vaults" terwijl de
    bucket onbereikbaar is -- de storing die als data leest."""
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({}, broken=["LIST"]), "vaults")
    for tool, args in (("list_vaults", {}), ("search_notes", {"query": "naald"})):
        resp = _call_tool(monkeypatch, store, tool, args)
        assert resp["error"]["code"] == -32002
        assert "result" not in resp


def test_a_broken_key_among_good_ones_surfaces(monkeypatch):
    """get_many mag een transportfout niet stil laten wegvallen zoals het een
    verdwenen sleutel doet: dan zou zoeken een notitie overslaan zonder dat
    iemand het merkt."""
    from vault_storage import StorageUnavailable, VaultStore
    objects = {f"{USER}/v/n{i:02d}.md": (f"naald {i}", f'"e{i}"') for i in range(20)}
    store = VaultStore(FakeS3(objects, broken=[f"{USER}/v/n07.md"]), "vaults")
    with pytest.raises(StorageUnavailable):
        store.get_many(list(objects))

    resp = _call_tool(monkeypatch, store, "search_notes", {"query": "naald"})
    assert resp["error"]["code"] == -32002


def test_an_empty_note_is_empty_and_not_missing():
    """Een leeg bestand is een bestaand bestand. NoteNotFound zou zeggen dat
    de notitie er niet is, en dat is een andere uitspraak."""
    from notes_tools import read_note
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/leeg.md": ("", '"z1"')}), "vaults")
    got = read_note(store, USER, "v", "leeg.md")
    assert got["content"] == ""
    assert got["revision"] == "z1"


USER_B = "9f2c1a44-0000-4000-8000-abcdefabcdef"


def _two_user_store():
    from vault_storage import VaultStore
    objects = {
        f"{USER}/mijn-vault/geheim.md": ("naald van user-a", '"a1"'),
        f"{USER}/los-a.md": ("losse naald van user-a", '"a2"'),
        f"{USER_B}/mijn-vault/geheim.md": ("naald van user-b", '"b1"'),
        f"{USER_B}/los-b.md": ("losse naald van user-b", '"b2"'),
        f"{USER_B}/alleen-b/CLAUDE.md": ("naald in regels van user-b", '"b3"'),
    }
    return VaultStore(FakeS3(objects), "vaults")


def test_one_user_never_sees_another_users_notes(monkeypatch):
    """De kern van de hele service. De server draait met een sleutel die de
    hele bucket kan lezen, dus de scheiding zit uitsluitend in de prefix die
    hier wordt voorgezet -- en die komt uit het JWT, niet uit de argumenten."""
    store = _two_user_store()

    vaults = _call_json(monkeypatch, store, "list_vaults", {})
    assert [v["vault"] for v in vaults] == ["mijn-vault", None]

    found = _call_json(monkeypatch, store, "search_notes", {"query": "naald"})
    assert found["total_matches"] == 2
    assert all("user-a" in h["snippet"] for h in found["hits"])

    note = _call_json(monkeypatch, store, "read_note",
                      {"vault": "mijn-vault", "path": "geheim.md"})
    assert note["content"] == "naald van user-a"

    # En de omgekeerde kant: user-b ziet niets van user-a.
    b_found = _call_json(monkeypatch, store, "search_notes", {"query": "naald"},
                         user_id=USER_B)
    assert b_found["total_matches"] == 3
    assert all("user-b" in h["snippet"] for h in b_found["hits"])


def test_no_tool_can_be_talked_into_another_users_prefix(monkeypatch):
    """Klimmen naar de buurman moet UnsafePath geven, niet zijn notitie."""
    resp = _call_tool(monkeypatch, _two_user_store(), "read_note",
                      {"vault": "mijn-vault", "path": f"../../{USER_B}/los-b.md"})
    assert resp["result"]["isError"] is True
    assert "losse naald van user-b" not in resp["result"]["content"][0]["text"]


# ── fase 2: 404-duiding, suggesties en schrijven ───────────────────────────

def test_a_supabase_404_on_a_live_bucket_is_a_missing_note():
    """De bug van 2026-09-08: Supabase geeft een lege 404, en elk verkeerd pad
    meldde daardoor 'Vault storage unavailable' -- een storing, terwijl er
    alleen een typefout in het pad zat."""
    from vault_storage import NoteNotFound, VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/bestaat.md": ("x", '"e"')}), "vaults")
    with pytest.raises(NoteNotFound):
        store.get(f"{USER}/v/weg.md")


def test_a_supabase_404_on_a_dead_bucket_is_still_a_storage_failure():
    """De grens die niet mag verschuiven. Een verdwenen bucket geeft dezelfde
    lege 404 als een ontbrekende sleutel; alleen de probe onderscheidt ze. Valt
    die weg, dan leest een storing weer als data -- het verbod uit deze module."""
    from vault_storage import StorageUnavailable, VaultStore
    store = VaultStore(FakeS3({}, broken=["LIST"]), "vaults")
    with pytest.raises(StorageUnavailable):
        store.get(f"{USER}/v/weg.md")


def test_the_bucket_probe_only_runs_on_the_miss_path():
    """Een geslaagde lezing mag geen extra call kosten."""
    from vault_storage import VaultStore
    fake = FakeS3({f"{USER}/v/a.md": ("x", '"e"')})
    probes = []
    fake.list_objects_v2 = lambda **kw: probes.append(1) or {"KeyCount": 0}
    VaultStore(fake, "vaults").get(f"{USER}/v/a.md")
    assert probes == []


def test_write_creates_a_new_note():
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({}), "vaults")
    revision = store.put(f"{USER}/v/nieuw.md", "hallo")
    assert revision
    assert store.get(f"{USER}/v/nieuw.md")[0] == "hallo"


def test_writing_over_an_existing_note_without_a_revision_is_refused():
    """Blind overschrijven is hoe een kennisbank stilletjes werk verliest."""
    from vault_storage import RevisionConflict, VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("oud", '"e1"')}), "vaults")
    with pytest.raises(RevisionConflict):
        store.put(f"{USER}/v/a.md", "nieuw")
    assert store.get(f"{USER}/v/a.md")[0] == "oud"


def test_writing_with_the_current_revision_succeeds():
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("oud", '"e1"')}), "vaults")
    _, revision = store.get(f"{USER}/v/a.md")
    store.put(f"{USER}/v/a.md", "nieuw", expected_revision=revision)
    assert store.get(f"{USER}/v/a.md")[0] == "nieuw"


def test_writing_with_a_stale_revision_is_refused():
    from vault_storage import RevisionConflict, VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("oud", '"e1"')}), "vaults")
    with pytest.raises(RevisionConflict):
        store.put(f"{USER}/v/a.md", "nieuw", expected_revision="verouderd")
    assert store.get(f"{USER}/v/a.md")[0] == "oud"


def test_a_revision_for_a_note_that_does_not_exist_is_refused():
    """Wijzen naar een revisie van een notitie die er niet is betekent dat de
    schrijver een andere notitie in gedachten heeft."""
    from vault_storage import RevisionConflict, VaultStore
    store = VaultStore(FakeS3({}), "vaults")
    with pytest.raises(RevisionConflict):
        store.put(f"{USER}/v/weg.md", "x", expected_revision='"e1"')


def test_write_note_refuses_to_leave_the_vault():
    import notes_tools
    from vault_paths import UnsafePath
    from vault_storage import VaultStore
    store = VaultStore(FakeS3({}), "vaults")
    with pytest.raises(UnsafePath):
        notes_tools.write_note(store, USER, "v", "../elders.md", "x")


def test_a_missing_note_suggests_the_right_path():
    """Precies de sessie van 2026-09-08: gevraagd werd
    'Trading/Concepts/LazyTheta-Lens-Mechanica.md', de notitie stond op
    'Concepts/...'. Zonder suggestie loopt de aanroeper dood."""
    import notes_tools
    from vault_storage import NoteNotFound, VaultStore
    store = VaultStore(FakeS3({
        f"{USER}/v/Concepts/LazyTheta-Lens-Mechanica.md": ("inhoud", '"e"'),
    }), "vaults")
    with pytest.raises(NoteNotFound) as excinfo:
        notes_tools.read_note(store, USER, "v", "Trading/Concepts/LazyTheta-Lens-Mechanica.md")
    assert "Concepts/LazyTheta-Lens-Mechanica.md" in str(excinfo.value)


def test_a_missing_note_without_a_near_match_says_how_to_search():
    import notes_tools
    from vault_storage import NoteNotFound, VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/iets-anders.md": ("x", '"e"')}), "vaults")
    with pytest.raises(NoteNotFound) as excinfo:
        notes_tools.read_note(store, USER, "v", "bestaat-niet.md")
    assert "search_notes" in str(excinfo.value)


def test_the_suggestion_finds_a_note_in_another_vault():
    """Vaak is niet de map fout maar de vault."""
    import notes_tools
    from vault_storage import NoteNotFound, VaultStore
    store = VaultStore(FakeS3({f"{USER}/andere/Concepts/X.md": ("x", '"e"')}), "vaults")
    with pytest.raises(NoteNotFound) as excinfo:
        notes_tools.read_note(store, USER, "v", "Concepts/X.md")
    assert "andere" in str(excinfo.value)


def test_a_conflict_names_the_path_not_the_storage_key():
    """De sleutel bevat de user-id en zegt de schrijver niets. Een melding die
    de lezer niet verder helpt is precies wat deze ronde heeft opgeruimd."""
    import notes_tools
    from vault_storage import RevisionConflict, VaultStore
    store = VaultStore(FakeS3({f"{USER}/v/a.md": ("oud", '"e1"')}), "vaults")
    with pytest.raises(RevisionConflict) as excinfo:
        notes_tools.write_note(store, USER, "v", "a.md", "nieuw")
    message = str(excinfo.value)
    assert USER not in message
    assert "a.md (vault: v)" in message
