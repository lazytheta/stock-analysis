"""Add the "Moat Cards" prompt to a user's pre-scan library, once.

Dry run by default; --apply writes. Inserts right after "Moat Analysis"
(or at the end if that prompt is missing) and leaves every other prompt
as it is.

    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
        python3 scripts/add_moat_cards_prompt.py --user-id <uuid> [--apply]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moat_cards


def insert_prompt(library):
    """(new_library, changed). Never mutates the input."""
    lib = [dict(p) for p in (library or [])]
    titles = [p.get("title") for p in lib]
    if moat_cards.TITLE in titles:
        return lib, False
    entry = {"title": moat_cards.TITLE, "prompt": moat_cards.PROMPT}
    at = titles.index("Moat Analysis") + 1 if "Moat Analysis" in titles else len(lib)
    lib.insert(at, entry)
    return lib, True


def should_write(prefs):
    """False when prefs has no non-empty ai_prompts list.

    config_store.load_user_prefs swallows read errors and returns the
    DEFAULT prefs on failure (no ai_prompts key at all), which looks
    exactly like "user has no prompt library yet". Writing in that case
    would silently create a stunted library instead of the real one, so
    refuse and let the caller re-run 'Load default prompts' in the app.
    """
    prompts = prefs.get("ai_prompts") if isinstance(prefs, dict) else None
    return isinstance(prompts, list) and len(prompts) > 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user-id", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    from supabase import create_client
    import config_store
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    prefs = config_store.load_user_prefs(client, user_id=args.user_id)
    if not should_write(prefs):
        print("No existing prompt library found (or the load failed); not writing "
              "anything. Use 'Load default prompts' in the app.")
        return
    new, changed = insert_prompt(prefs.get("ai_prompts") or [])
    if not changed:
        print("Moat Cards already in the library; nothing to do.")
        return
    print("Would insert Moat Cards at position",
          [p["title"] for p in new].index(moat_cards.TITLE) + 1, "of", len(new))
    if args.apply:
        prefs["ai_prompts"] = new
        config_store.save_user_prefs(client, prefs, user_id=args.user_id)
        print("Saved.")


if __name__ == "__main__":
    main()
