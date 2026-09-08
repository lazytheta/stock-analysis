"""De drie leestools, gebouwd op VaultStore.

Zoeken heeft geen index. Dat kan omdat de schaal het toelaat: 1,63 MB over
244 bestanden, dus alles ophalen kost per zoekopdracht een fractie van de
gratis egress. Een index zou een tweede kopie van de notities zijn, en
tweede kopieen van dezelfde waarheid hebben in dit project al drie keer een
bug opgeleverd.

Herzien zodra de gezamenlijke markdown boven ~10 MB komt of het aantal
bestanden boven ~1.000.
"""

from __future__ import annotations

from vault_paths import NOTE_SUFFIX, UnsafePath, storage_key, vault_prefix
from vault_storage import NoteNotFound, RevisionConflict

CLAUDE_MD = "CLAUDE.md"


def _is_note(key: str) -> bool:
    return key.lower().endswith(NOTE_SUFFIX)


def _split_key(user_id: str, key: str) -> tuple[str | None, str] | None:
    """(vault, pad) van een objectsleutel, of None als die niet te duiden is.

    De bucket kan sleutels bevatten die deze server nooit zelf zou bouwen --
    '<user>/../x.md' bijvoorbeeld, of een pad met een leeg segment. Zo'n
    sleutel is niet adresseerbaar en dus niet aan te bieden aan read_note, maar
    dat is geen reden om de hele tool te laten falen: eerder legde één zo'n
    sleutel list_vaults plat met UnsafePath en verdwenen álle vaults. Overslaan.

    `vault=None` betekent: los onder de gebruikersprefix.
    """
    prefix = vault_prefix(user_id)
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    vault, separator, path = rest.partition("/")
    if not separator:
        vault, path = None, rest
    if not path:
        return None                       # 'vault/' zonder bestandsnaam
    if vault is not None:
        try:
            vault_prefix(user_id, vault)  # keurt de vaultnaam als padsegment
        except UnsafePath:
            return None
    if any(p in ("", ".", "..") for p in path.split("/")):
        return None
    return vault, path


def snippet(text: str, query: str, radius: int = 120) -> str:
    """Het stukje tekst rond de treffer, zodat zichtbaar is waarom een
    notitie in de uitkomst staat."""
    lowered = text.lower()
    at = lowered.find((query or "").lower())
    if at < 0:
        head = text[: radius * 2].strip()
        return head + ("…" if len(text) > radius * 2 else "")
    start = max(0, at - radius)
    end = min(len(text), at + len(query) + radius)
    body = text[start:end].strip()
    return ("…" if start > 0 else "") + body + ("…" if end < len(text) else "")


def list_vaults(store, user_id: str) -> list[dict]:
    """Welke vaults er zijn, hoeveel notities erin staan, en of er een
    CLAUDE.md ligt met de schrijfregels van die vault.

    Staan er notities buiten elke vaultmap -- los onder de gebruikersprefix --
    dan volgt als laatste een vermelding met `vault: null`. Meestal betekent
    dat een verkeerd ingestelde remote prefix in de synchronisatieplugin.

    `null` en geen sentinel-string, omdat een vaultnaam een padsegment is en
    `null` daar per definitie niet mee kan botsen. De vorige sentinel
    "(unfiled)" kon dat wel: bestond er een échte vault met die naam, dan wees
    een treffer of leesverzoek naar de verkeerde notitie. read_note(vault=None)
    leest zo'n losse notitie gewoon."""
    keys = store.list_keys(vault_prefix(user_id))
    vaults: dict[str, dict] = {}
    unfiled_count = 0

    for key in keys:
        split = _split_key(user_id, key)
        if split is None:
            continue                 # niet te duiden: overslaan, niet fataal
        vault, path = split

        if vault is None:
            if _is_note(key):
                unfiled_count += 1
            continue

        # Vaults met alleen bijlagen moeten ook verschijnen, met notes: 0
        entry = vaults.setdefault(vault, {"vault": vault, "notes": 0, "claude_md": None})
        if _is_note(key):
            entry["notes"] += 1
            if path == CLAUDE_MD:
                entry["claude_md"] = CLAUDE_MD

    result = [vaults[v] for v in sorted(vaults)]
    if unfiled_count > 0:
        result.append({"vault": None, "notes": unfiled_count, "claude_md": None})
    return result


def _describe(vault: str | None, path: str) -> str:
    return f"{path} (vault: {vault})" if vault else f"{path} (los onder de gebruiker)"


def _near_matches(store, user_id: str, path: str, limit: int = 3) -> list[str]:
    """Notities met dezelfde bestandsnaam, waar ze ook staan.

    De aanleiding: op 2026-09-08 werd vier keer
    'Trading/Concepts/LazyTheta-Lens-Mechanica.md' gevraagd terwijl de notitie
    op 'Concepts/...' stond. Wikilinks als [[LazyTheta-Lens-Mechanica]] dragen
    geen map, dus de map wordt geraden -- en raden zit er meestal één niveau
    naast. De bestandsnaam is dan het enige dat wél klopt.
    """
    target = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    found: list[str] = []
    for key in store.list_keys(vault_prefix(user_id)):
        if not _is_note(key):
            continue
        split = _split_key(user_id, key)
        if split is None:
            continue
        found_vault, found_path = split
        if found_path.rsplit("/", 1)[-1].lower() == target:
            found.append(_describe(found_vault, found_path))
        if len(found) >= limit:
            break
    return found


def _miss_message(store, user_id: str, vault: str | None, path: str) -> str:
    """Waarom de notitie er niet is, en wat de aanroeper nu moet doen.

    Een kale sleutel als foutmelding laat de lezer met lege handen achter, en
    dan valt hij terug op een andere bron -- wat precies de schade van
    2026-09-08 was: de canonieke notitie bleef onbereikbaar en er is drie
    maanden oude sessielog voor in de plaats gelezen.
    """
    head = f"Notitie niet gevonden: {_describe(vault, path)}."
    try:
        matches = _near_matches(store, user_id, path)
    except Exception:
        matches = []          # de opslag hikt; de misser zelf staat al vast
    if matches:
        return head + " Bedoelde je: " + " · ".join(matches) + "?"
    return head + " Zoek het juiste pad met search_notes."


def read_note(store, user_id: str, vault: str | None, path: str) -> dict:
    """Eén notitie, met revisie. De revisie doet in fase 1 niets, maar staat
    er zodat het contract bij het toevoegen van schrijven niet verandert.

    `vault=None` leest een notitie die los onder de gebruikersprefix staat --
    precies wat list_vaults en search_notes met `vault: null` aanduiden."""
    key = storage_key(user_id, vault, path)
    try:
        text, revision = store.get(key)
    except NoteNotFound:
        raise NoteNotFound(_miss_message(store, user_id, vault, path)) from None
    return {"vault": vault, "path": path, "revision": revision, "content": text}


def write_note(store, user_id: str, vault: str | None, path: str, content: str,
               revision: str | None = None) -> dict:
    """Maak een notitie aan of werk hem bij, en geef de nieuwe revisie terug.

    Zonder `revision` mag alleen een nieuwe notitie ontstaan. Bestaat het pad
    al, dan volgt RevisionConflict: dan moet de schrijver hem eerst lezen en de
    gelezen revisie meegeven. Dat is bewust onhandig -- de aanroeper is een
    model dat een pad kan raden, en een geraden pad dat toevallig bestaat mag
    geen bestaande notitie wissen.

    Let op wat dit niet is: deze server schrijft naar Supabase Storage, niet
    naar de map op de laptop. De notitie verschijnt pas in Obsidian nadat de
    synchronisatieplugin een ronde heeft gedraaid.
    """
    key = storage_key(user_id, vault, path)
    new_revision = store.put(key, content, expected_revision=revision)
    return {"vault": vault, "path": path, "revision": new_revision,
            "bytes": len(content.encode("utf-8"))}


def search_notes(store, user_id: str, query: str, vault: str | None = None,
                 max_results: int = 20) -> dict:
    """Zoek op inhoud. Zonder vault doorzoekt hij alles, inclusief losse notities.

    Geeft een omhullende dict terug:

        {"hits": [...], "returned": n, "total_matches": N, "truncated": bool}

    en niet, zoals eerder, een kale lijst treffers. Een kale lijst kan niet
    zeggen dat hij is afgekapt: met 50 treffers en max_results=20 kwamen er 20
    terug, altijd het alfabetische begin, zonder markering. Op "heb ik ooit
    over X geschreven?" is dat een onvolledig antwoord dat niet van een
    volledig antwoord te onderscheiden is -- dezelfde soort stille onwaarheid
    als een lege lijst bij een onbereikbare bucket.

    Waarom een dict en geen extra laatste element in de lijst: een lijst waarin
    het laatste element geen treffer is, is een lijst waarover len() liegt en
    waar elke gewone iteratie overheen struikelt. De dict maakt de drie feiten
    (deze treffers, zoveel gevonden, wel/niet afgekapt) los benoembaar, en de
    MCP-laag geeft hem ongewijzigd door als JSON.

    Daarom telt hij ook dóór na de limiet: total_matches is het aantal notities
    dat de zoekterm bevat, niet het aantal dat je terugkrijgt.
    """
    needle = (query or "").strip()
    empty = {"hits": [], "returned": 0, "total_matches": 0, "truncated": False}
    if not needle:
        return empty

    keys = [k for k in store.list_keys(vault_prefix(user_id, vault)) if _is_note(k)]
    contents = store.get_many(keys)

    hits: list[dict] = []
    total = 0
    limit = max(0, int(max_results))
    for key in keys:
        text = contents.get(key)
        if text is None or needle.lower() not in text.lower():
            continue

        # In welke vault ligt deze notitie? None betekent: los onder de
        # gebruikersprefix. Geen sentinel-string, zodat de treffer niet kan
        # botsen met een échte vault die toevallig zo heet -- read_note op de
        # treffer levert altijd dezelfde notitie op als waar hij vandaan komt.
        # Een sleutel die niet te duiden is wordt overgeslagen: hem als treffer
        # aanbieden zou een notitie adverteren die read_note niet kan openen.
        split = _split_key(user_id, key)
        if split is None:
            continue
        found_in, path = split

        total += 1
        # Doortellen na de limiet, maar geen snippets meer bouwen.
        if len(hits) < limit:
            hits.append({
                "vault": found_in,
                "path": path,
                "snippet": snippet(text, needle),
            })

    return {"hits": hits, "returned": len(hits), "total_matches": total,
            "truncated": total > len(hits)}


__all__ = ["NoteNotFound", "RevisionConflict", "list_vaults", "read_note",
           "search_notes", "snippet", "write_note"]
