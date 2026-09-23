# Ljuskollen evals (written before build)

Each eval is pass/fail. `ljuskollen.py` runs all five at the end of every run and writes the result to `audit_log.jsonl`.

| ID | Name | Pass rule | On fail |
|----|------|-----------|---------|
| E1 | Traceable | Every number on the card is derived only from responses recorded in `audit_log.jsonl` (URL, UTC timestamp, HTTP status 200, raw file path). No hardcoded or default numbers. | Card is not produced. |
| E2 | Sane baseline | Median of June daily irradiation (Wh/m²) >= 10 x median of December daily irradiation, over 1999 to last year. | Pipeline stops. No card. Report. |
| E3 | 8-second card | Max 3 content lines, each <= 70 characters, plus exactly 1 action from the fixed list. No medical claim words (cure, treat, diagnose, prevent, depression). Footer disclaimer present verbatim. | Card is not sent. |
| E4 | Honest layers | Line 3 text matches the lookup table exactly for the current season, is labelled "reflection, not forecast", and contains no causal words (causes, because of, makes you, will bring). Nakshatra only if the cosmic-timing skill returned it. | Line 3 is dropped. |
| E5 | Private | Only inputs are kommun-level coordinates (2 decimals max). No health, mood or user data fields exist in any stored file. `COMPLIANCE.md` present. | Card is not sent. |
