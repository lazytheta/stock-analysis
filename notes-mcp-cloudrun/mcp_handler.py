"""Stateless MCP JSON-RPC-dispatcher voor de Obsidian-vaults.

Elke /mcp-aanroep draagt een JWT; de middleware in main.py haalt daar de
user_id uit en zet hem in scope["state"]. Die user_id gaat naar elke tool.
Een user_id in de argumenten wordt genegeerd -- dat is de les uit het
load_credential-lek, waar een ongefilterde lezing de rijen van alle
gebruikers matchte.

Fase 2 schrijft ook. write_note eist de gelezen revisie voor een bestaande
notitie, zodat een geraden pad dat toevallig bestaat niets kan wissen.
"""

from __future__ import annotations

import logging
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import notes_tools
from vault_paths import UnsafePath
from vault_storage import (NoteNotFound, RevisionConflict, StorageUnavailable,
                           VaultStore, make_client)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "notes-mcp"
SERVER_VERSION = "2.0.0"

logger = logging.getLogger(__name__)

_CACHED_STORE = None


def _store() -> VaultStore:
    global _CACHED_STORE
    if _CACHED_STORE is None:
        _CACHED_STORE = VaultStore(make_client(),
                                   os.environ.get("VAULT_BUCKET", "vaults"))
    return _CACHED_STORE


async def _tool_list_vaults(user_id: str, args: dict):
    return notes_tools.list_vaults(_store(), user_id)


async def _tool_search_notes(user_id: str, args: dict):
    # search_notes geeft een omhullende dict terug (hits + total_matches +
    # truncated) in plaats van een kale lijst; die gaat ongewijzigd als JSON
    # naar de client, zodat afkappen zichtbaar is in het antwoord zelf.
    return notes_tools.search_notes(
        _store(), user_id, query=args["query"], vault=args.get("vault"),
        max_results=int(args.get("max_results", 20)))


async def _tool_read_note(user_id: str, args: dict):
    # args.get: `vault` mag null of afwezig zijn, en betekent dan "los onder
    # de gebruikersprefix" -- de vorm waarin list_vaults en search_notes zulke
    # notities aanduiden sinds de sentinel-string weg is.
    return notes_tools.read_note(_store(), user_id, args.get("vault"), args["path"])


async def _tool_write_note(user_id: str, args: dict):
    return notes_tools.write_note(
        _store(), user_id, args.get("vault"), args["path"], args["content"],
        revision=args.get("revision"))


TOOL_HANDLERS = {
    "list_vaults": _tool_list_vaults,
    "search_notes": _tool_search_notes,
    "read_note": _tool_read_note,
    "write_note": _tool_write_note,
}

TOOLS: list[dict] = [
    {
        "name": "list_vaults",
        "description": (
            "List the Obsidian vaults, how many notes each holds, and whether "
            "it carries a CLAUDE.md with that vault's own conventions. Read "
            "that file before writing into a vault with write_note. An entry with "
            "`vault: null` means notes sit outside any vault folder, directly "
            "under the user prefix — usually a misconfigured remote prefix in "
            "the sync plugin. Pass that same null to read_note to read them."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_notes",
        "description": (
            "Search note contents across the vaults. Returns "
            "{hits, returned, total_matches, truncated}: each hit carries the "
            "vault, the path and the surrounding text. `total_matches` counts "
            "every matching note, also beyond `max_results`, so `truncated: "
            "true` tells you the answer is partial — raise `max_results` or "
            "narrow the query before concluding anything from it. A hit with "
            "`vault: null` sits outside any vault; pass that null straight to "
            "read_note. Pass `vault` to narrow the search when you already "
            "know where to look."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "vault": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_note",
        "description": (
            "Read one note in full. `path` is relative to the vault root, for "
            "example 'Tickers/DECK.md'. Pass `vault` exactly as list_vaults or "
            "search_notes reported it; null (or omitted) reads a note that sits "
            "outside any vault. Returns the content and a `revision` — keep that "
            "revision if you intend to write the note back. A path that does not "
            "exist reports which notes carry the same filename, so a wrong folder "
            "is a correctable miss and not a dead end."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault": {"type": ["string", "null"]},
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_note",
        "description": (
            "Create a note, or update one you have just read. To create, pass "
            "`path` and `content` and omit `revision`. To update, first read_note "
            "and pass back the `revision` it returned — writing over an existing "
            "note without its current revision is refused, and so is writing with "
            "a stale one, because a guessed path that happens to exist must never "
            "erase a real note. Check the vault's CLAUDE.md (see list_vaults) for "
            "its conventions before writing. This writes to the synced copy in "
            "storage, so the note appears in the Obsidian app only after the sync "
            "plugin next runs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault": {"type": ["string", "null"]},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "revision": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
]


async def _handle_one(message: dict, user_id: str | None) -> dict | None:
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled",
                  "notifications/progress"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        if not user_id:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32001, "message": "Authenticated user required"}}
        tool_name = params.get("name")
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"}}
        try:
            result = await handler(user_id, params.get("arguments") or {})
        except (UnsafePath, NoteNotFound, RevisionConflict, KeyError, ValueError) as e:
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": f"Error: {e}"}],
                               "isError": True}}
        except StorageUnavailable as e:
            # Nadrukkelijk geen lege uitkomst: de vault is niet leeg, hij is
            # onbereikbaar, en dat verschil moet de aanroeper zien.
            logger.exception("storage unavailable")
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32002, "message": f"Vault storage unavailable: {e}"}}
        except Exception:
            logger.exception("Tool %s failed", tool_name)
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32603, "message": "Internal server error"}}

        import json
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text",
                                        "text": json.dumps(result, ensure_ascii=False)}]}}

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


async def mcp_endpoint(request: Request) -> Response:
    if request.method == "GET":
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "GET not supported"}},
                            status_code=405)
    if request.method == "DELETE":
        return Response(status_code=200)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "Parse error"}},
                            status_code=400)

    user_id = request.scope.get("state", {}).get("user_id")

    if isinstance(body, list):
        responses = [r for r in
                     [await _handle_one(m, user_id) for m in body] if r is not None]
        return JSONResponse(responses) if responses else Response(status_code=202)

    if not isinstance(body, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "Invalid request"}},
                            status_code=400)

    response = await _handle_one(body, user_id)
    return JSONResponse(response) if response is not None else Response(status_code=202)
