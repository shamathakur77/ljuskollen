# Ljuskollen compliance note

**Purpose:** a daily wellbeing nudge about outdoor daylight in one Swedish kommun. Wellbeing nudge, not a medical device. It does not diagnose, treat or monitor any condition.

**Data sources and licences**
- SMHI STRÅNG (history, global irradiance). SMHI open data, licence CC BY 4.0 (attribution: SMHI). To re-confirm at opendata.smhi.se.
- Forecast: SMHI forecast API if it offers radiation, otherwise Open-Meteo (CC BY 4.0, attribution: Open-Meteo.com). The card states which one was used.

**What is stored:** raw API responses (weather data only), a computed baseline table, the card text, and an append-only audit log of each run (time, URLs called, HTTP status, card, eval results).

**What is not stored:** no names, emails, street addresses, mood, sleep, health or usage data. Location is kommun-level coordinates only (2 decimals). No cookies, trackers or accounts.

**Lawful basis:** no personal data is processed, so GDPR processing obligations do not apply to the pipeline itself. If email or push delivery is added later, the recipient address becomes personal data and needs its own note.
