from datetime import datetime
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models"

def features(row):
    d = datetime.fromisoformat(row["timestamp"])
    return [d.hour, d.weekday(), d.month, float(row.get("temperature") or 0), float(row.get("humidity") or 0), float(row.get("wind_speed") or 0), float(row.get("rainfall") or 0)]

def load_artifacts():
    p = MODEL_DIR / "model.joblib"
    if not p.exists(): return None, None
    import joblib
    return joblib.load(p), json.loads((MODEL_DIR / "metrics.json").read_text())

def predict(row):
    model, metrics = load_artifacts()
    if not model: return None
    vals = model.predict([features(row)])[0]
    return {"pm25": max(0, float(vals[0])), "pm10": max(0, float(vals[1])), "model_version": metrics.get("version", "unknown")}
