import json, math
from datetime import datetime
from app.data.store import read_rows
from app.ml.pipeline import features, MODEL_DIR

def main():
    rows = read_rows()
    if len(rows) < 30:
        print(f"Insufficient data: {len(rows)} valid rows; need at least 30. No model trained."); return 2
    try:
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
        import joblib
    except ImportError:
        print("scikit-learn/joblib unavailable; install backend/requirements.txt. No model trained."); return 2
    rows.sort(key=lambda r: r["timestamp"])
    cut = max(1, int(len(rows) * .8)); train, test = rows[:cut], rows[cut:]
    if len(test) < 2: print("Insufficient chronological holdout rows."); return 2
    X = [features(r) for r in train]; y = [[float(r["pm25"]), float(r["pm10"])] for r in train]
    model = RandomForestRegressor(n_estimators=160, min_samples_leaf=2, random_state=42, n_jobs=-1)
    model.fit(X, y)
    pred = model.predict([features(r) for r in test]); actual = [[float(r["pm25"]), float(r["pm10"])] for r in test]
    metrics = {"version":"rf-v1", "trained_at":datetime.now().astimezone().isoformat(), "samples_train":len(train), "samples_test":len(test), "test_start":test[0]["timestamp"], "test_end":test[-1]["timestamp"], "features":["hour","weekday","month","temperature","humidity","wind_speed","rainfall"], "pollutants":{}}
    for j, name in enumerate(("pm25", "pm10")):
        truth=[a[j] for a in actual]; forecast=[float(p[j]) for p in pred]; baseline=[float(train[-1][name])]*len(test)
        rmse=lambda a,b: math.sqrt(mean_squared_error(a,b))
        metrics["pollutants"][name]={"mae":mean_absolute_error(truth,forecast),"rmse":rmse(truth,forecast),"r2":r2_score(truth,forecast) if len(truth)>1 else None,"baseline_mae":mean_absolute_error(truth,baseline),"baseline_rmse":rmse(truth,baseline)}
    MODEL_DIR.mkdir(parents=True, exist_ok=True); joblib.dump(model, MODEL_DIR/"model.joblib"); (MODEL_DIR/"metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
