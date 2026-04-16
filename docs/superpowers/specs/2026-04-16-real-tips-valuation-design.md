# Feature Spec: Real (TIPS) Valuation Mode — LazyTheta DCF

## Problem

De huidige DCF-tool werkt uitsluitend met nominale inputs: nominale risk-free rate (10-jaars Treasury), nominale revenue growth, en nominale terminal growth. Dit leidt tot drie problemen:

1. **Instabiliteit**: de intrinsic value schommelt mee met de nominale rente, die wordt beïnvloed door tijdelijke factoren (inflatieschokken, geopolitiek, oil shocks) die niets te maken hebben met de earning power van het bedrijf.

2. **Dubbeltelling bij bedrijven met pricing power**: een hogere nominale rente (door inflatie) verhoogt de WACC, maar de revenue growth assumptions worden niet evenredig verhoogd — terwijl bedrijven als PEP hun prijzen wél doorberekenen. Het model straft de noemer zonder de teller te belonen.

3. **Inconsistentie bij updates**: elke keer dat de rente verandert, moet de gebruiker handmatig de WACC aanpassen. De revenue growth (die ook een inflatie-component bevat) wordt doorgaans niet mee-aangepast, wat leidt tot een mismatch.

## Oplossing

Een `valuation_basis` parameter die de tool laat schakelen tussen nominale en reële waardering.

### Nieuwe config parameter

```python
valuation_basis: "nominal" | "real"  # default: "nominal"
```

Wanneer `"real"`:
- De tool gebruikt de 10-jaars TIPS yield als risk-free rate
- Revenue growth wordt geïnterpreteerd als reële groei (ex-inflatie)
- Terminal growth wordt geïnterpreteerd als reële groei
- WACC wordt berekend op basis van de reële Rf
- Alle outputs worden gelabeld als "reëel"

Wanneer `"nominal"` (default, huidige gedrag):
- Geen veranderingen aan bestaande functionaliteit

## Implementatie

### 1. fetch_tips_yield — Nieuwe functie in gather_data.py
- Haal de huidige 10-jaars TIPS yield op (bron: FRED series DFII10)
- Bereken breakeven inflatie: nominal_rf - tips_rf

### 2. build_config aanpassingen
- Nieuwe parameter: `valuation_basis="nominal"`
- Bij "real": gebruik TIPS yield als risk_free_rate
- Bij "real": trek breakeven_inflation af van revenue_growth
- Bij "real": default terminal_growth = 0.005 (i.p.v. 0.025)
- Sla referentie-velden op: nominal_risk_free_rate, breakeven_inflation, nominal_revenue_growth

### 3. Validatieregels bij "real"
- risk_free_rate moet positief zijn en onder 4%
- terminal_growth moet lager zijn dan risk_free_rate
- terminal_growth boven 1.5% triggert waarschuwing
- breakeven_inflation moet tussen 0.5% en 5% liggen

### 4. MCP server aanpassingen
- Nieuwe parameter `valuation_basis` in build_dcf_config tool
- Doorgeven aan gather_data.build_config()

### 5. Output labeling
- valuation_basis, nominal_risk_free_rate, breakeven_inflation in output

### 6. convert_to_real utility (nice-to-have)
- Converteer bestaande nominale config naar reële basis
