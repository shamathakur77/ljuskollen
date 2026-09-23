"""Ljuskollen: daily light-debt tracker + 7-day low-light outlook for one Swedish location.

Loop 0 (manual):  python ljuskollen.py loop0
Rule self-test:   python ljuskollen.py selftest   (no network, no numbers on any card)

Every HTTP call is logged to audit_log.jsonl (append-only). Raw responses go to raw/.
No number reaches the card unless it came from a logged 200 response (E1).
"""
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# ---------------------------------------------------------------- config
KOMMUN = "Stockholm (example location)"
LAT, LON = 59.33, 18.07          # kommun-level, 2 decimals max (E5)
TZ = ZoneInfo("Europe/Stockholm")
HIST_START_YEAR = 1999

# SMHI STRÅNG. Parameter IDs to be re-verified at opendata.smhi.se/apidocs/strang
STRANG_URL = ("https://opendata-download-metanalys.smhi.se/api/category/strang1g/"
              "version/1/geotype/point/lon/{lon}/lat/{lat}/parameter/{p}/data.json")
STRANG_GLOBAL_IRRADIANCE = 117   # W/m², hourly, instantaneous at full hour, UTC (to verify)

# Forecast fallback (only if the current SMHI forecast API has no radiation)
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"
MJ_TO_WH = 1_000_000 / 3600      # 1 MJ/m² = 277.78 Wh/m²

ROOT = Path(__file__).parent
RAW = ROOT / "raw"
AUDIT = ROOT / "audit_log.jsonl"

FOOTER = ("Not medical advice. If low mood persists, talk to a doctor. "
          "Light therapy: check with a doctor first if you have bipolar disorder.")
ACTIONS = {
    "lamp": "Lamp within 30 min of waking, 20 to 30 min",
    "walk": "Take your walk between 12 and 13",
    "out":  "Get outside before noon",
}
LOOKUP = {  # only source for line 3 (E4). Do not add lore.
    "dark":    ("Nov-Jan", "Isa (ice, stillness)", "Samhain ~1 Nov, Yule at the winter solstice",
                "Dakshinayana, the sun's southern half, until Makar Sankranti mid-Jan"),
    "rising":  ("Feb-Apr", "Berkano (birch, new growth)", "Imbolc ~1 Feb, Ostara at the spring equinox",
                "Uttarayana, the sun's northern half"),
    "bright":  ("May-Jul", "Sowilo (sun)", "Beltane ~1 May, Litha at the summer solstice",
                "Uttarayana until Karka Sankranti mid-Jul"),
    "falling": ("Aug-Oct", "Jera (year, harvest)", "Lughnasadh ~1 Aug, Mabon at the autumn equinox",
                "Dakshinayana"),
}
SEASON_BY_MONTH = {11: "dark", 12: "dark", 1: "dark", 2: "rising", 3: "rising", 4: "rising",
                   5: "bright", 6: "bright", 7: "bright", 8: "falling", 9: "falling", 10: "falling"}


# ---------------------------------------------------------------- audit + fetch
def audit(event: dict) -> None:
    event = {"ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    with AUDIT.open("a", encoding="utf-8") as f:           # append-only
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def fetch_json(url: str, params: dict | None = None, tag: str = "", retries: int = 4,
               timeout: int = 60) -> tuple[object, Path]:
    """Resilient GET: retries with exponential backoff, timeout, loud logging, raw save."""
    RAW.mkdir(exist_ok=True)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            audit({"type": "http", "tag": tag, "url": r.url, "status": r.status_code,
                   "attempt": attempt, "bytes": len(r.content)})
            if r.status_code == 200:
                raw_path = RAW / f"{tag}_{hashlib.sha1(r.url.encode()).hexdigest()[:10]}.json"
                raw_path.write_bytes(r.content)
                return r.json(), raw_path
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code in (400, 401, 403, 404):
                break                                        # not retryable
        except requests.RequestException as e:
            last_err = repr(e)
            audit({"type": "http_error", "tag": tag, "url": url, "params": params,
                   "attempt": attempt, "error": last_err})
        print(f"!! FETCH FAILED [{tag}] attempt {attempt}/{retries}: {last_err}", file=sys.stderr)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"FETCH FAILED [{tag}] {url} :: {last_err}")


# ---------------------------------------------------------------- history + baseline
def parse_strang(payload) -> pd.Series:
    """STRÅNG point JSON -> hourly Series (UTC index). Fails loudly on unknown schema."""
    rows = payload if isinstance(payload, list) else payload.get("values") or payload.get("data")
    if not rows or not isinstance(rows, list):
        raise ValueError(f"Unexpected STRÅNG schema: {str(payload)[:300]}")
    k_time = next(k for k in ("date_time", "dateTime", "time") if k in rows[0])
    s = pd.Series({pd.Timestamp(r[k_time]).tz_convert("UTC") if pd.Timestamp(r[k_time]).tzinfo
                   else pd.Timestamp(r[k_time], tz="UTC"): float(r["value"]) for r in rows})
    s[s <= -900] = float("nan")          # SMHI missing-value sentinel (-999), found in Loop 0
    return s.clip(lower=0).sort_index()


def pull_history(today: date) -> pd.Series:
    parts = []
    for year in range(HIST_START_YEAR, today.year + 1):
        end = min(date(year, 12, 31), today)
        payload, _ = fetch_json(
            STRANG_URL.format(lon=LON, lat=LAT, p=STRANG_GLOBAL_IRRADIANCE),
            params={"from": f"{year}-01-01", "to": end.isoformat(), "interval": "hourly"},
            tag=f"strang117_{year}")
        parts.append(parse_strang(payload))
    return pd.concat(parts).sort_index()


def daily_wh(hourly_wm2: pd.Series) -> pd.Series:
    """Sum of hourly W/m² x 1 h, grouped by Stockholm local date. Days with <20 hours dropped."""
    local = hourly_wm2.dropna().tz_convert(TZ)
    g = local.groupby(local.index.date)
    d = g.sum()[g.count() >= 20]
    d.index = pd.to_datetime(d.index)
    return d


def build_baseline(daily: pd.Series, last_year: int) -> pd.DataFrame:
    """Per day-of-year median and p25 over HIST_START_YEAR..last_year, ±7-day window."""
    hist = daily[(daily.index.year >= HIST_START_YEAR) & (daily.index.year <= last_year)]
    doy = hist.index.dayofyear.where(hist.index.dayofyear <= 365, 365)
    out = []
    for d in range(1, 366):
        dist = ((doy - d + 182) % 365) - 182
        win = hist[(abs(dist) <= 7)]
        out.append({"doy": d, "median": win.median(), "p25": win.quantile(0.25), "n": len(win)})
    return pd.DataFrame(out).set_index("doy")


def eval_e2(daily: pd.Series, last_year: int) -> tuple[bool, str]:
    h = daily[daily.index.year <= last_year]
    jun, dec = h[h.index.month == 6].median(), h[h.index.month == 12].median()
    ok = dec > 0 and jun >= 10 * dec
    return ok, f"June median {jun:.0f} Wh/m², Dec median {dec:.0f} Wh/m², ratio {jun/dec:.1f}x"


# ---------------------------------------------------------------- forecast
def pull_forecast() -> tuple[pd.Series, str]:
    """7-day daily irradiation forecast (Wh/m²). SMHI checked first in Loop 0; fallback Open-Meteo."""
    payload, _ = fetch_json(OPENMETEO_URL, params={
        "latitude": LAT, "longitude": LON, "daily": "shortwave_radiation_sum",
        "timezone": "Europe/Stockholm", "forecast_days": 8}, tag="openmeteo_fc")
    s = pd.Series(payload["daily"]["shortwave_radiation_sum"],
                  index=pd.to_datetime(payload["daily"]["time"]), dtype="float") * MJ_TO_WH
    return s, "Open-Meteo shortwave_radiation_sum (fallback)"


# ---------------------------------------------------------------- card
def season_line(d: date, nakshatra: str | None = None) -> str:
    rune, fest, vedic = season_parts(d)
    nak = f" · {nakshatra}" if nakshatra else ""
    return f"{rune} · {fest} · {vedic}{nak} (reflection, not forecast)"


def season_parts(d: date) -> tuple[str, str, str]:
    """Only today's slice of the lookup table: rune, the most recent Wheel festival, Vedic half."""
    _, rune, wheel, vedic = LOOKUP[SEASON_BY_MONTH[d.month]]
    first, second = [w.split(" ")[0] for w in wheel.split(", ")]
    past_second = d.month % 3 == 0 and d.day >= 21 or d.month % 3 == 1 and d.month not in (2, 5, 8, 11)
    half = vedic.split(",")[0].split(" until")[0]
    # table says the half flips at Makar Sankranti (mid-Jan) and Karka Sankranti (mid-Jul); ~14 Jan / ~16 Jul
    if d.month == 1 and d.day >= 15:
        half = "Uttarayana"
    if d.month == 7 and d.day >= 17:
        half = "Dakshinayana"
    return rune, (second if past_second else first), half


def choose_action(debt_pct: float, streak: bool, today_pct: float) -> str:
    if debt_pct <= -30 or streak:
        return ACTIONS["lamp"]
    if today_pct >= 110:
        return ACTIONS["walk"]
    return ACTIONS["out"]


def streak_ahead(fc: pd.Series, base: pd.DataFrame, n: int = 3) -> bool:
    low = [v < base.loc[min(i.dayofyear, 365), "p25"] for i, v in fc.items()]
    run = best = 0
    for x in low:
        run = run + 1 if x else 0
        best = max(best, run)
    return best >= n


def build_card(d: date, today_pct: float, debt_pct: float, streak: bool, part_of_month: str,
               nakshatra: str | None = None) -> dict:
    l1 = f"Today (forecast): {today_pct:.0f}% of a normal {part_of_month} day"
    l2 = f"Light debt this week: {debt_pct:+.0f}% vs normal" + (" · 3+ dim days ahead" if streak else "")
    return {"lines": [l1, l2, season_line(d, nakshatra)],
            "action": choose_action(debt_pct, streak, today_pct), "footer": FOOTER}


def eval_e3(card: dict) -> tuple[bool, str]:
    banned = ("cure", "treat", "diagnos", "prevent", "depression")
    text = " ".join(card["lines"] + [card["action"]]).lower()
    ok = (len(card["lines"]) <= 3 and card["action"] in ACTIONS.values()
          and card["footer"] == FOOTER and not any(b in text for b in banned)
          and all(len(l) <= 110 for l in card["lines"]))
    return ok, "3 lines + 1 action + footer" if ok else "card shape/claims check failed"


def eval_e4(card: dict, d: date) -> tuple[bool, str]:
    l3 = card["lines"][2]
    causal = ("causes", "because of", "makes you", "will bring")
    rune, fest, vedic = season_parts(d)
    ok = rune in l3 and vedic in l3 and fest in l3 and "reflection, not forecast" in l3 and not any(c in l3.lower() for c in causal)
    return ok, "line 3 from lookup table only, labelled"


def eval_e5() -> tuple[bool, str]:
    ok = (round(LAT, 2) == LAT and round(LON, 2) == LON and (ROOT / "COMPLIANCE.md").exists())
    return ok, "kommun-level coords, no personal fields, compliance note present"


# ---------------------------------------------------------------- self-test (rules only)
def selftest() -> None:
    d = date(2026, 9, 23)
    assert SEASON_BY_MONTH[d.month] == "falling" and "Jera" in season_line(d)
    for dd, want in [((1, 5), "Yule"), ((2, 3), "Imbolc"), ((3, 25), "Ostara"), ((6, 1), "Beltane"),
                     ((6, 22), "Litha"), ((9, 10), "Lughnasadh"), ((9, 23), "Mabon"), ((11, 2), "Samhain"),
                     ((12, 22), "Yule")]:
        assert season_parts(date(2026, *dd))[1] == want, (dd, season_parts(date(2026, *dd)))
    assert season_parts(date(2026, 1, 20))[2] == "Uttarayana"
    assert season_parts(date(2026, 7, 20))[2] == "Dakshinayana"
    assert choose_action(-40, False, 80) == ACTIONS["lamp"]
    assert choose_action(0, True, 120) == ACTIONS["lamp"]
    assert choose_action(0, False, 120) == ACTIONS["walk"]
    assert choose_action(0, False, 90) == ACTIONS["out"]
    print("selftest: rule logic OK (no data used, no card produced)")


def part_of_month(d: date) -> str:
    return ("early" if d.day <= 10 else "mid" if d.day <= 20 else "late") + "-" + d.strftime("%B")


def loop0() -> None:
    today = datetime.now(TZ).date()
    run_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run = {"type": "run", "mode": "loop0", "kommun": KOMMUN}
    try:
        hourly = pull_history(today)
        daily = daily_wh(hourly)
        base = build_baseline(daily, today.year - 1)
        e2_ok, e2_msg = eval_e2(daily, today.year - 1)
        run["E2"] = ["PASS" if e2_ok else "FAIL", e2_msg]
        if not e2_ok:
            raise SystemExit(f"E2 FAIL, pipeline broken: {e2_msg}")

        latest = daily.index.max().date()                    # STRÅNG lags: latest = yesterday
        med = lambda ts: base.loc[min(ts.dayofyear, 365), "median"]
        yday_pct = 100 * daily.iloc[-1] / med(daily.index[-1])
        last7 = daily.iloc[-7:]
        debt_pct = 100 * sum(v - med(i) for i, v in last7.items()) / sum(med(i) for i in last7.index)

        today_ts = pd.Timestamp(today)
        try:
            fc_all, fc_src = pull_forecast()
            if today_ts not in fc_all.index:
                raise RuntimeError("forecast has no value for today")
            today_pct, basis = 100 * fc_all[today_ts] / med(today_ts), "forecast"
            fc = fc_all[fc_all.index.date > today][:7]
            streak = streak_ahead(fc, base)
        except Exception as e:                               # degrade, never invent
            audit({"type": "degraded", "reason": f"forecast unavailable: {e!r}"[:300]})
            fc_src, today_pct, basis = "none (forecast unavailable)", yday_pct, "yesterday"
            fc, streak = pd.Series(dtype="float"), False

        nak = None
        try:                                                 # cosmic-timing skill, if present
            from cosmic_timing import compute_day
            nak = compute_day()["astro"]["nakshatra"]
        except Exception as e:
            audit({"type": "skill_skipped", "skill": "cosmic-timing", "error": repr(e)})

        card = build_card(today, today_pct, debt_pct, streak, part_of_month(today), nak)
        if basis == "yesterday":
            card["lines"][0] = card["lines"][0].replace("Today (forecast)", "Yesterday (measured)")
        card["data_note"] = (f"Line 1: {fc_src if basis == 'forecast' else 'SMHI STRÅNG, ' + str(latest)}. Line 2: SMHI STRÅNG actuals "
                             f"{last7.index[0].date()} to {latest} (yesterday was {yday_pct:.0f}% of normal).")
        # E1: every source tag must have a logged HTTP 200 in this run and a raw file on disk
        logged = [json.loads(l) for l in AUDIT.read_text(encoding="utf-8").splitlines()]
        ok200 = {e["tag"] for e in logged if e.get("type") == "http" and e.get("status") == 200
                 and e["ts_utc"] >= run_start}
        need = {f"strang117_{y}" for y in range(HIST_START_YEAR, today.year + 1)} | ({"openmeteo_fc"} if basis == "forecast" else set())
        e1_ok = need <= ok200 and all(any(RAW.glob(f"{t}_*.json")) for t in need)
        results = {"E1": (e1_ok, f"{len(need & ok200)}/{len(need)} source calls logged 200 with raw file"),
                   "E2": (e2_ok, e2_msg),
                   "E3": eval_e3(card), "E4": eval_e4(card, today), "E5": eval_e5()}
        run.update(card=card, evals={k: ["PASS" if v[0] else "FAIL", v[1]] for k, v in results.items()})

        (ROOT / "baseline.csv").write_text(base.to_csv())
        (ROOT / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2))
        if all(v[0] for v in results.values()):              # page only updates when every eval passes
            write_page_data(today, today_pct, debt_pct, streak, nak, fc, last7, base, basis)
        else:
            run["status"] = "EVAL FAIL: page not updated"
        plot(base, today, fc)
        print(json.dumps(run, ensure_ascii=False, indent=2))
    except Exception as e:
        run["status"] = f"FAILED: {e!r}"
        print(f"!! LOOP 0 STOPPED: {e!r}", file=sys.stderr)
        raise
    finally:
        audit(run)


def write_page_data(today, today_pct, debt_pct, streak, nak, fc, last7, base, basis="forecast") -> None:
    """data.json for the companion page (app/index.html fetches it)."""
    sr = ss = None
    try:
        from cosmic_timing import compute_day
        p = compute_day(); sr, ss = p["sunrise"], p["sunset"]
    except Exception as e:
        audit({"type": "skill_skipped", "skill": "cosmic-timing sunrise", "error": repr(e)})
    rune, fest, vedic = season_parts(today)
    med = lambda ts: base.loc[min(ts.dayofyear, 365), "median"]
    p25 = lambda ts: base.loc[min(ts.dayofyear, 365), "p25"]
    row = lambda s: [[str(i.date()), round(100 * v / med(i)), bool(v < p25(i))] for i, v in s.items()]
    out = {
        "kommun": KOMMUN, "date": today.isoformat(),
        "updated": datetime.now(TZ).strftime("%-d %b %Y, %H:%M"),
        "todayPct": round(today_pct), "basis": basis, "partOfMonth": part_of_month(today), "debtPct": round(debt_pct),
        "streak": bool(streak), "action": next(k for k, v in ACTIONS.items() if v == choose_action(debt_pct, streak, today_pct)),
        "sunrise": sr, "sunset": ss,
        "reflection": {"rune": rune, "wheel": fest, "vedic": vedic, "nakshatra": nak},
        "ahead": row(fc), "past": row(last7),
        "sources": "Light: SMHI STRÅNG open data (history), Open-Meteo.com (forecast). Sunrise: Stockholm, cosmic-timing. Evals E1 to E5 passed.",
    }
    (ROOT / "app").mkdir(exist_ok=True)
    (ROOT / "app" / "data.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


def plot(base: pd.DataFrame, today: date, fc: pd.Series) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.fill_between(base.index, base["p25"] / 1000, base["median"] / 1000, alpha=.25, label="25th pct to median")
    ax.plot(base.index, base["median"] / 1000, label="median (1999 to last year)")
    ax.axvline(today.timetuple().tm_yday, ls="--", c="k", label="today")
    ax.scatter([i.dayofyear for i in fc.index], fc.values / 1000, s=14, c="C3", label="7-day forecast")
    ax.set(xlabel="day of year", ylabel="kWh/m² per day", title=f"Daily global irradiation, {KOMMUN} (SMHI STRÅNG)")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(ROOT / "baseline_chart.png", dpi=150)


if __name__ == "__main__":
    selftest() if sys.argv[1:] == ["selftest"] else loop0()
