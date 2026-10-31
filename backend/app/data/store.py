import csv
import math
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "observations.csv"
FIELDS = ["timestamp", "pm25", "pm10", "temperature", "humidity", "wind_speed", "rainfall", "station", "latitude", "longitude"]

def read_rows():
    if not DATA.exists(): return []
    with DATA.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return sorted(rows, key=lambda x: x["timestamp"])

def validate_rows(rows):
    clean, seen, rejected = [], set(), 0
    for row in rows:
        try:
            stamp = datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
            values = {k: (float(row[k]) if row.get(k) not in (None, "") else None) for k in ("pm25", "pm10", "temperature", "humidity", "wind_speed", "rainfall", "latitude", "longitude")}
            if values["pm25"] is None or values["pm10"] is None or not all(v is None or math.isfinite(v) for v in values.values()) or min(values["pm25"], values["pm10"]) < 0: raise ValueError()
            station = str(row.get("station") or "Unspecified")[:100]
            key = (stamp.isoformat(), station)
            if key in seen: continue
            seen.add(key)
            clean.append({"timestamp": stamp.isoformat(), **values, "station": station})
        except (ValueError, TypeError, OverflowError): rejected += 1
    return clean, rejected

def save_rows(rows):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    existing, _ = validate_rows(read_rows() + rows)
    with DATA.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS); writer.writeheader(); writer.writerows(existing)
    return len(existing)
