"""Local daily station observations and Diwali-aligned comparisons."""
import csv
import io
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

DB = Path(__file__).resolve().parents[2] / "data" / "historical.sqlite3"
DIWALI_DATES = {
    2022: date(2022, 10, 24),
    2023: date(2023, 11, 12),
    2024: date(2024, 10, 31),
    2025: date(2025, 10, 20),
    2026: date(2026, 11, 8),
}
DATE_SOURCES = {
    2022: "https://www.timeanddate.com/holidays/india/diwali",
    2023: "https://www.timeanddate.com/holidays/india/diwali",
    2024: "https://cag.gov.in/uploads/media/List-of-holidays-2024-068c3d96130a564-11046917.pdf",
    2025: "https://www.timeanddate.com/holidays/india/diwali",
    2026: "https://www.indiapost.gov.in/holidays-list",
}
METRICS = ("pm25", "pm10", "aqi")
METRIC_LABELS = {"pm25": "PM₂.₅", "pm10": "PM₁₀", "aqi": "AQI"}
CONCENTRATION_UNIT = "µg/m³"


def today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


@contextmanager
def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init():
    with connect() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS daily_observations(
              id INTEGER PRIMARY KEY,
              city TEXT NOT NULL,
              station TEXT NOT NULL,
              observed_date TEXT NOT NULL,
              pm25 REAL,
              pm10 REAL,
              aqi INTEGER,
              pm_unit TEXT NOT NULL DEFAULT 'ug/m3',
              source_name TEXT NOT NULL,
              source_url TEXT,
              imported_at TEXT NOT NULL,
              UNIQUE(city, station, observed_date)
            )"""
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(daily_observations)")}
        if "pm_unit" not in columns:
            db.execute("ALTER TABLE daily_observations ADD COLUMN pm_unit TEXT NOT NULL DEFAULT 'ug/m3'")


def observation_key(city, station, day):
    return (" ".join(city.casefold().split()), " ".join(station.casefold().split()), day)


def existing_keys():
    with connect() as db:
        rows = db.execute("SELECT city, station, observed_date FROM daily_observations").fetchall()
    return {observation_key(row["city"], row["station"], row["observed_date"]) for row in rows}


def validate_csv(raw, source_name, source_url=None, unit=CONCENTRATION_UNIT):
    """Parse and validate a daily CSV without writing any records."""
    normalized_unit = str(unit or "").strip().lower().replace("μ", "µ").replace(" ", "")
    if normalized_unit not in {"ug/m3", "µg/m3", "µg/m³"}:
        raise ValueError("PM₂.₅ and PM₁₀ must be supplied in µg/m³. Convert the source file before importing.")
    source_name = " ".join(str(source_name or "").split())
    if not source_name:
        raise ValueError("Enter the data source name")
    source_url = str(source_url or "").strip() or None
    if source_url:
        parsed_url = urlsplit(source_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Source URL must be a valid http(s) link")
    try:
        raw_rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("Could not read CSV as UTF-8 with a valid header") from exc
    if not raw_rows:
        raise ValueError("CSV has no data rows")

    normalized_rows = []
    for row in raw_rows:
        normalized_rows.append({(key or "").strip().lower(): value for key, value in row.items() if key})
    fields = set(normalized_rows[0])
    date_field = "date" if "date" in fields else "timestamp" if "timestamp" in fields else None
    if not date_field or not {"city", "station"}.issubset(fields):
        raise ValueError("Required headers: date (or timestamp), city, station, and at least one of pm25, pm10, aqi")
    provided_metrics = [metric for metric in METRICS if metric in fields]
    if not provided_metrics:
        raise ValueError("Add at least one pollutant column: pm25, pm10, or aqi")

    cutoff = today()
    clean = []
    rejected = 0
    duplicate_rows = 0
    seen = set()
    for row in normalized_rows:
        try:
            raw_day = str(row.get(date_field) or "").strip()
            if date_field == "date":
                day = date.fromisoformat(raw_day)
            else:
                try:
                    day = datetime.fromisoformat(raw_day.replace("Z", "+00:00")).date()
                except ValueError:
                    day = date.fromisoformat(raw_day)
            city = " ".join(str(row.get("city") or "").split())
            station = " ".join(str(row.get("station") or "").split())
            if not city or not station:
                raise ValueError("City and station are required")
            if len(city) > 100 or len(station) > 150:
                raise ValueError("City or station name exceeds the supported length")
            if day > cutoff:
                raise ValueError("Future observations are not accepted")
            row_unit = str(row.get("unit") or row.get("pm_unit") or "").strip().lower().replace("μ", "µ").replace(" ", "")
            if row_unit and row_unit not in {"ug/m3", "µg/m3", "µg/m³"}:
                raise ValueError("CSV unit column must use µg/m³ for PM₂.₅/PM₁₀")
            values = {}
            for metric in METRICS:
                raw_value = row.get(metric)
                value = float(raw_value) if raw_value not in (None, "") else None
                if value is not None and (not math.isfinite(value) or value < 0):
                    raise ValueError("Measurements must be finite and non-negative")
                values[metric] = value
            if values["aqi"] is not None and (values["aqi"] > 500 or not values["aqi"].is_integer()):
                raise ValueError("AQI must be an integer from 0 to 500")
            if all(values[metric] is None for metric in METRICS):
                raise ValueError("Each row needs at least one pollutant value")
            key = observation_key(city, station, day.isoformat())
            if key in seen:
                duplicate_rows += 1
                continue
            seen.add(key)
            row_url = (row.get("source_url") or source_url or "").strip() or None
            if row_url:
                parsed_url = urlsplit(row_url)
                if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                    raise ValueError("Source URL must be a valid http(s) link")
            clean.append({
                "city": city,
                "station": station,
                "date": day.isoformat(),
                **values,
                "pm_unit": "ug/m3",
                "source_name": " ".join(str(row.get("source_name") or source_name).split())[:200],
                "source_url": row_url[:500] if row_url else None,
            })
        except (ValueError, TypeError, OverflowError):
            rejected += 1

    metric_days = {metric: sum(row[metric] is not None for row in clean) for metric in METRICS}
    stations = sorted({(row["city"], row["station"]) for row in clean})
    dates = [row["date"] for row in clean]
    return {
        "rows": clean,
        "valid_rows": len(clean),
        "rejected_rows": rejected,
        "duplicate_rows": duplicate_rows,
        "date_field": date_field,
        "provided_metrics": provided_metrics,
        "metric_days": metric_days,
        "stations": [{"city": city, "station": station} for city, station in stations],
        "date_from": min(dates) if dates else None,
        "date_to": max(dates) if dates else None,
        "unit": CONCENTRATION_UNIT,
    }


def catalog():
    with connect() as db:
        groups = db.execute(
            """SELECT city, station, count(*) records,
                      min(observed_date) start, max(observed_date) end,
                      count(pm25) pm25_days, count(pm10) pm10_days, count(aqi) aqi_days,
                      group_concat(DISTINCT source_name) sources,
                      group_concat(DISTINCT source_url) source_urls
               FROM daily_observations GROUP BY city, station ORDER BY city, station"""
        ).fetchall()
        all_records = db.execute(
            "SELECT city, station, observed_date, pm25, pm10, aqi FROM daily_observations ORDER BY city, station, observed_date"
        ).fetchall()
    by_station = {}
    for row in all_records:
        by_station.setdefault((row["city"], row["station"]), []).append(row)
    output = []
    for group in groups:
        key = (group["city"], group["station"])
        records = by_station.get(key, [])
        diwali_years = []
        for year, festival in DIWALI_DATES.items():
            start = festival - timedelta(days=7)
            end = festival + timedelta(days=7)
            within = [r for r in records if start.isoformat() <= r["observed_date"] <= end.isoformat()]
            diwali_years.append({
                "year": year,
                "diwali_date": festival.isoformat(),
                "record_days": len(within),
                "pm25_days": sum(r["pm25"] is not None for r in within),
                "pm10_days": sum(r["pm10"] is not None for r in within),
                "aqi_days": sum(r["aqi"] is not None for r in within),
                "available": bool(within),
            })
        output.append({
            "city": group["city"],
            "station": group["station"],
            "records": group["records"],
            "start": group["start"],
            "end": group["end"],
            "pm25_days": group["pm25_days"],
            "pm10_days": group["pm10_days"],
            "aqi_days": group["aqi_days"],
            "pm_unit": CONCENTRATION_UNIT,
            "sources": group["sources"].split(",") if group["sources"] else [],
            "source_urls": group["source_urls"].split(",") if group["source_urls"] else [],
            "diwali_years": diwali_years,
            "available_diwali_years": [item["year"] for item in diwali_years if item["available"]],
        })
    return output


def compare(city, station, years, days=3):
    years = sorted(set(years))
    if len(years) < 2:
        raise ValueError("Select at least two years")
    if any(year not in DIWALI_DATES for year in years):
        raise ValueError("Choose years from 2022 through 2026")
    if not 1 <= days <= 7:
        raise ValueError("Choose between 1 and 7 days per comparison window")

    as_of = today()
    results = []
    with connect() as db:
        for year in years:
            festival = DIWALI_DATES[year]
            bounds = {
                "before": (festival - timedelta(days=days), festival - timedelta(days=1)),
                "during": (festival, festival),
                "after": (festival + timedelta(days=1), festival + timedelta(days=days)),
            }
            windows = {}
            for window_name, (start, end) in bounds.items():
                elapsed_end = min(end, as_of)
                elapsed_days = max(0, (elapsed_end - start).days + 1)
                expected_days = (end - start).days + 1
                future_days = expected_days - elapsed_days
                if elapsed_days:
                    rows = db.execute(
                        """SELECT observed_date, pm25, pm10, aqi, pm_unit, source_name, source_url
                           FROM daily_observations
                           WHERE lower(city)=lower(?) AND lower(station)=lower(?)
                             AND observed_date BETWEEN ? AND ? ORDER BY observed_date""",
                        (city, station, start.isoformat(), elapsed_end.isoformat()),
                    ).fetchall()
                else:
                    rows = []
                observed_dates = {row["observed_date"] for row in rows}
                metrics = {}
                for metric in METRICS:
                    values = sorted(float(row[metric]) for row in rows if row[metric] is not None)
                    valid_days = len(values)
                    metrics[metric] = {
                        "mean": sum(values) / valid_days if valid_days else None,
                        "median": (values[valid_days // 2] if valid_days % 2 else (values[valid_days // 2 - 1] + values[valid_days // 2]) / 2) if valid_days else None,
                        "max": max(values) if values else None,
                        "valid_days": valid_days,
                        "missing_days": max(0, elapsed_days - valid_days),
                        "future_days": future_days,
                    }
                if future_days and not elapsed_days:
                    status = "upcoming"
                elif future_days:
                    status = "partial"
                elif rows:
                    status = "available"
                else:
                    status = "no_data"
                windows[window_name] = {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "effective_end": elapsed_end.isoformat() if elapsed_days else None,
                    "status": status,
                    "expected_days": expected_days,
                    "elapsed_days": elapsed_days,
                    "observed_days": len(observed_dates),
                    "missing_days": max(0, elapsed_days - len(observed_dates)),
                    "future_days": future_days,
                    "metrics": metrics,
                    "records": [dict(row) for row in rows],
                    "sources": sorted({row["source_name"] for row in rows}),
                    "source_urls": sorted({row["source_url"] for row in rows if row["source_url"]}),
                    "provenance": "Imported station data; source labels are uploader-supplied and not independently verified by this app.",
                }
            comparisons = {metric: _year_comparison(windows, metric) for metric in METRICS}
            results.append({
                "year": year,
                "festival_date": festival.isoformat(),
                "festival_status": "upcoming" if festival > as_of else "past",
                "windows": windows,
                "comparisons": comparisons,
            })
    return {
        "city": city,
        "station": station,
        "years": years,
        "days_each_side": days,
        "as_of": as_of.isoformat(),
        "data_kind": "observed",
        "pm_unit": CONCENTRATION_UNIT,
        "results": results,
        "summary": _summary(results),
    }


def _year_comparison(windows, metric):
    before = windows["before"]["metrics"][metric]
    during = windows["during"]["metrics"][metric]
    base = {
        "before_mean": before["mean"],
        "during_mean": during["mean"],
        "before_valid_days": before["valid_days"],
        "during_valid_days": during["valid_days"],
        "absolute_change": None,
        "change_percent": None,
    }
    if before["mean"] is None or during["mean"] is None:
        return {**base, "status": "insufficient_data", "reason": "A valid mean is missing from one or both periods."}
    absolute_change = during["mean"] - before["mean"]
    if before["mean"] == 0:
        return {**base, "absolute_change": absolute_change, "status": "zero_baseline", "reason": "Percentage change is undefined because the before-period mean is zero."}
    return {
        **base,
        "absolute_change": absolute_change,
        "change_percent": absolute_change / before["mean"] * 100,
        "status": "compared",
        "reason": None,
    }


def _summary(results):
    summary = {}
    for metric in METRICS:
        comparisons = []
        zero_baseline = []
        for year in results:
            item = year["comparisons"][metric]
            if item["status"] == "compared":
                comparisons.append({"year": year["year"], **item})
            elif item["status"] == "zero_baseline":
                zero_baseline.append({"year": year["year"], **item})
        summary[metric] = {
            "comparisons": comparisons,
            "zero_baseline_years": [item["year"] for item in zero_baseline],
            "message": _trend(comparisons, zero_baseline, metric),
        }
    return summary


def _trend(comparisons, zero_baseline, metric):
    label = METRIC_LABELS[metric]
    if not comparisons and not zero_baseline:
        return f"Not enough observed {label} data in both the before and Diwali-day periods to compare."
    if not comparisons:
        changes = [item["absolute_change"] for item in zero_baseline]
        average = sum(changes) / len(changes)
        if average > 0:
            direction = "increased"
        elif average < 0:
            direction = "decreased"
        else:
            direction = "stayed at zero"
        return f"{label} {direction} from a zero before-period mean in {len(zero_baseline)} comparable year(s); a percentage change cannot be calculated from a zero baseline."
    changes = [item["change_percent"] for item in comparisons]
    average = sum(changes) / len(changes)
    if abs(average) < 5:
        direction = "was broadly similar"
    else:
        direction = "increased" if average > 0 else "decreased"
    message = f"Across {len(comparisons)} year(s) with non-zero before-period means, {label} {direction} by an average {abs(average):.1f}% on Diwali day versus the before period."
    if zero_baseline:
        message += f" Percentage change is undefined for {len(zero_baseline)} other comparable year(s) because the before-period mean was zero."
    return message + " This is descriptive, not proof of cause."


init()
