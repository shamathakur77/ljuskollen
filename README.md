# Ljuskollen

A daily light check for one location in Sweden. Every morning it compares the day's sunlight with 27 years of normal for that date and gives you one card: 3 lines and 1 action.

Built as a **governed agent**: evals written before the code, an append-only audit trail, safe fallbacks and a compliance note.

## What it does
- Pulls hourly global irradiance from **SMHI STRÅNG** (1999 to today) with retries, backoff and loud error logging.
- Builds a baseline for each calendar day: median and 25th percentile over a ±7-day window.
- Gets a 7-day forecast from **Open-Meteo** (`shortwave_radiation_sum`).
- Computes: today vs normal, light debt (rolling 7 days vs median), and a dim-streak alert (3+ low-light days ahead).
- Writes the card, runs 5 pass/fail evals, and only publishes if all pass.

## Run it
```bash
pip install pandas requests matplotlib
python ljuskollen.py selftest   # rule logic, no network
python ljuskollen.py loop0      # full run: history, baseline, forecast, card, evals
```
Change `KOMMUN`, `LAT`, `LON` at the top for your own location (use kommun-level coordinates, 2 decimals).

## Evals (see EVALS.md)
E1 traceable numbers · E2 sane baseline (June ≥ 10× December) · E3 8-second card · E4 honest reflection layer · E5 privacy

## Things the data taught me
- SMHI STRÅNG marks missing hours with `-999`. The pipeline treats them as missing instead of summing them.
- STRÅNG runs about a day behind, so "today" is a forecast and is labelled that way.
- If the forecast source is down, the card falls back to yesterday's measured light and says so.

## Data and licences
SMHI open data (attribute SMHI). Open-Meteo.com (attribute Open-Meteo). Check each provider's current terms before commercial use.

Not medical advice. Wellbeing nudge, not a medical device.

Built by Shama Thakur · Sovereign by Source
