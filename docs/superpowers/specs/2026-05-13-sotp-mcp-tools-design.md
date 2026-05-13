# SOTP-editing tools voor remote MCP — Design

**Date:** 2026-05-13
**Status:** Approved (brainstorming)
**Related:** `2026-05-13-sotp-lens-design.md` (lens engine), `2026-05-09-lazytheta-mcp-cloudrun-design.md` (MCP host)

## Probleem

De SOTP-lens is in de Streamlit-app volledig editable (segments, corporate overhead, lens-weight) maar via de remote MCP alleen impliciet beschikbaar: `calculate_multi_lens_valuation` rekent SOTP wel uit als `cfg.sotp.segments` bestaat, maar er is geen tool om segments toe te voegen, bij te werken of te verwijderen. Lens-weight kan momenteel niet expliciet op `sotp` worden gezet (description van `update_lens_weights` noemt alleen `dcf, multiples, historical, reverse_dcf, dividend`).

Doel: SOTP volledig stuurbaar maken vanuit Claude/MCP-clients zonder de hele watchlist-config te hoeven meesturen.

## Architectuur

Drie nieuwe tools + één uitbreiding op een bestaande, alle in `lazytheta-mcp-cloudrun/mcp_handler.py`. Pattern volgt exact `update_valuation_inputs` / `update_lens_weights`:

1. Tool-definitie in de `TOOLS` lijst (schema + description).
2. Dispatch in `handle_tool_call`.
3. Persist via `config_store.save_config(client, ticker, cfg, user_id)`.

Geen wijziging aan `valuation_lenses.py` nodig — de lens-engine leest al uit `cfg["sotp"]`. Geen wijziging aan `config_store.py` voor de SOTP-tools zelf: `sotp` staat al in `_GUARDED_KEYS_RESTORE_MISSING_ONLY` (regel 66), dus empty `segments: []` blijft persistent (legitieme user-intent).

## Tools

### `update_sotp_segments`

Upsert-by-name met partial merge.

**Args:**
- `ticker: string` (required)
- `segments: array` (required, non-empty) — lijst van objecten met velden:
  - `name: string` (required) — segment-naam, case-insensitive match key
  - `ev_mid: number` (required voor nieuwe segments, > 0)
  - `ev_low: number` (optional, ≥ 0)
  - `ev_high: number` (optional, ≥ 0)
  - `revenue: number` (optional, metadata)
  - `operating_margin: number` (optional, decimal 0–1)
  - `implied_multiple_mid: number` (optional)
  - `rationale: string` (optional)

**Semantiek:**
- Voor elk input-segment: match op `name` (strip + lowercase) tegen bestaande `cfg.sotp.segments`.
- Match gevonden → merge gegeven velden in bestaand segment (alleen niet-null velden uit input).
- Geen match → append als nieuw segment. Vereist `ev_mid > 0` (consistent met Streamlit `streamlit_app.py:5496`).
- Andere segmenten blijven ongemoeid.
- Initialiseert `cfg["sotp"] = {"segments": [], "corporate_overhead_ev_adjustment": 0}` als nog niet aanwezig.

**Validatie:**
- Leeg `segments` array → error (geen no-op om typo's/lege LLM-calls te vangen).
- Segment zonder `name` → error met index.
- Nieuw segment zonder geldige `ev_mid` → error met segment-naam.
- Negatieve EV-waardes → error.

### `remove_sotp_segment`

Delete by name. Idempotent.

**Args:**
- `ticker: string` (required)
- `name: string` (required)

**Semantiek:**
- Case-insensitive match. Verwijder eerste match uit `cfg.sotp.segments`.
- Geen match → no-op (idempotent — consistent met "remove last peer = legitiem leeg" pattern).
- Geen initialisatie nodig — als `cfg.sotp` of `segments` niet bestaat → no-op.

### `set_sotp_corporate_overhead`

Scalar setter voor `corporate_overhead_ev_adjustment`.

**Args:**
- `ticker: string` (required)
- `value: number` (required, $M, vaak negatief)

**Semantiek:**
- Schrijft `cfg["sotp"]["corporate_overhead_ev_adjustment"] = value`.
- Initialiseert `cfg["sotp"]` indien afwezig, met `segments: []`.

### `update_lens_weights` — uitbreiding

`sotp` toevoegen als valid key.

**Aanpassing:**
- Description regel 245-268 in `mcp_handler.py`: lijst `dcf, multiples, historical, reverse_dcf, dividend` → `dcf, multiples, historical, reverse_dcf, dividend, sotp`.
- Implementatie-check vereist: als `config_store.update_lens_weights` (of waar de dispatch landt) een whitelist heeft, daar `sotp` toevoegen. Als het al een dict-merge is op willekeurige keys, alleen description-update.

## Data-flow

```
LLM call
  → MCP tool dispatch
  → config_store.get_config(ticker, user_id)
  → mutate cfg["sotp"] in-memory
  → config_store.save_config(client, ticker, cfg, user_id)
  → return summary dict
```

**Return shape (alle drie SOTP-tools):**
```json
{
  "ticker": "AMZN",
  "sotp": {
    "segments": [...],
    "corporate_overhead_ev_adjustment": -5000
  },
  "segment_count": 4
}
```

Niet de hele cfg terugsturen — context-zuinig. LLM kan via `get_config` of `calculate_multi_lens_valuation` verder kijken.

## Errors & edge cases

| Scenario | Gedrag |
|---|---|
| Ticker niet in watchlist | `ToolError` met msg "Ticker X not in watchlist" (zelfde patroon als bestaande update-tools) |
| `segments` leeg in `update_sotp_segments` | Error — geen no-op |
| Nieuw segment zonder `ev_mid > 0` | Error met segment-naam |
| Negative `ev_low`/`ev_mid`/`ev_high` | Error |
| `remove_sotp_segment` op niet-bestaande naam | No-op (idempotent) |
| `value` voor overhead = non-number | Schema-level rejection |
| Concurrent writes door 2 sessies | Erft bestaande save_config gedrag (last-writer-wins). Buiten scope. |

## Testing

Volgt patroon van `lazytheta-mcp-cloudrun/test_app.py`. Per tool minimaal:

**`update_sotp_segments`:**
- Happy path 1: upsert nieuw segment (geen bestaande sotp).
- Happy path 2: merge bestaand segment (alleen `rationale` updaten, andere velden ongemoeid).
- Happy path 3: meerdere segments in één call (mix nieuw + update).
- Edge: lege segments-array → error.
- Edge: segment zonder name → error.
- Edge: nieuw segment zonder ev_mid → error.
- Edge: ticker niet in watchlist → error.

**`remove_sotp_segment`:**
- Happy: remove bestaand segment.
- Edge: remove niet-bestaande naam → no-op (geen error).
- Edge: case-insensitive match werkt.
- Edge: cfg zonder sotp → no-op.

**`set_sotp_corporate_overhead`:**
- Happy: zet waarde, lees terug.
- Edge: initialiseert sotp dict als afwezig.

**`update_lens_weights` met sotp:**
- Happy: `{sotp: 0.15}` wordt correct opgeslagen en gebruikt door `calculate_multi_lens_valuation`.

**Integration:**
- Round-trip: `update_sotp_segments` → `calculate_multi_lens_valuation` → output bevat `sotp` lens met fv_low/mid/high.

## Out of scope

- Bulk-replace tool voor SOTP (afgewezen tijdens brainstorming — risico op wegspoelen).
- SOTP-segments validatie tegen 10-K segmentation (geen externe data-call).
- Auto-fill van segment-EVs uit yfinance/EDGAR (geen public API hiervoor).
- Optimistic locking / concurrent-write detection (geen issue gemeld; bestaande tools hebben dit ook niet).

## Acceptatiecriteria

1. Vanuit Claude Code MCP-sessie kan ik voor een watchlist-ticker:
   - Een nieuw segment toevoegen met één tool-call.
   - Een bestaand segment partial-updaten (alleen rationale wijzigen) zonder andere velden te verliezen.
   - Een segment verwijderen.
   - Corporate overhead instellen.
   - SOTP-lens activeren via `update_lens_weights({sotp: 0.15})`.
2. `calculate_multi_lens_valuation` na een SOTP-update levert correcte `sotp` lens output (fv_low/mid/high + details).
3. Alle nieuwe tests in `test_app.py` slagen.
4. Bestaande 81 tests in `test_tastytrade_api.py` + `test_ibkr_api.py` blijven groen (geen regressie).
5. Ruff lint passeert (`python3 -m ruff check .`).
