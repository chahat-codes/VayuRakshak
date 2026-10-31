# VAYURAKSHAK AI

**Predict. Explore. Prepare.** A small, transparent air-quality intelligence MVP.

## Run locally

Python 3.10+ is recommended.

```sh
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. The API docs are at `/docs`. By default the app requests Open-Meteo forecast data for Delhi. Set `VAYU_LATITUDE`, `VAYU_LONGITUDE`, and `VAYU_LOCATION` to change the default. Internet access is needed for the provider; otherwise the app shows an error and CSV imports remain available.

## Historical CSV and model training

Upload a CSV from **Data & model** or call `POST /api/data/import` with a multipart `file`. Required headers: `timestamp,pm25,pm10`; optional: `temperature,humidity,wind_speed,rainfall,station,latitude,longitude`. Timestamps must be ISO 8601, pollutant units are µg/m³. Invalid/negative pollutant values are skipped and duplicate station/timestamps are deduplicated. Imported data is stored locally in `backend/data/observations.csv`.

Train with `python -m app.ml.train` from `backend/`. The pipeline uses chronological holdout evaluation and saves the model/metrics when scikit-learn is installed and there are at least 30 valid records. Metrics are reported from the actual holdout only. With no sufficient data, training exits with a clear message; the forecast endpoint reports a persistence baseline instead of fabricating an ML result.

## Data and scientific limits

- Open-Meteo Air Quality API values are modelled grid estimates, **not CPCB station measurements**. Provider availability and freshness are shown in the app. See [Air Quality API](https://open-meteo.com/en/docs/air-quality-api) and [terms](https://open-meteo.com/en/terms). The free service is intended for non-commercial use; review licensing before deployment.
- No official CPCB station API integration is claimed. Import licensed CPCB or other station CSV observations for local analysis.
- AQI is intentionally unavailable unless valid official AQI is supplied; this MVP does not derive AQI from incomplete pollutant data or non-compliant averaging windows.
- Diwali analysis is computed from imported observations only. It is descriptive and does not establish fireworks causality.
- Digital Twin scenario outputs are labelled hypothetical. Without a validated causal model, the service refuses emission-reduction scenarios and only allows weather perturbations within conservative bounds when a trained model is available.
- The starter dataset is empty. The application does not contain invented sensor readings, model metrics, locations, or training results.

## Architecture

FastAPI serves the responsive static UI and JSON API. `app/data` validates and persists observations; `app/ml` contains feature engineering, chronological evaluation and optional Random Forest training. Data and model artifacts stay local and should not be committed.

## Green Community local mode

Communities, memberships, pledges, challenge completions, point awards, events, RSVPs, posts, images, likes, comments, and reports are saved in `backend/data/community.sqlite3` (images in `backend/data/uploads/`). The first run starts with no groups or participation; the UI shows an empty state until people create real records. Community point values are fixed in `app/data/community.py`; a unique database constraint prevents repeated completion from earning points again. Points are participation credits and do not represent pollution reduction.

There is no sign-in or identity verification in this local MVP. Profile IDs are stored in the browser and names are self-selected; they can be reset or impersonated. Event organisers are not verified, and reports are saved as pending but have no staffed moderation queue. Do not use this anonymous setup for a public production community without adding authentication, server-side authorization, rate limits, and moderation operations. SQLite and uploaded images remain on the machine running the backend.

## Diwali historical comparison

The Diwali comparison page accepts one daily CSV row per city, monitoring station, and date. Required columns are `date,city,station` plus at least one of `pm25,pm10,aqi`; `timestamp` is accepted instead of `date`. Leave unavailable pollutant values empty. Values must be non-negative, AQI must be an integer from 0 to 500, and dates after the local current date are rejected. Re-imports for the same city/station/date are reported as duplicates and do not overwrite earlier observations. Data is stored in `backend/data/historical.sqlite3`.

Select at least two years and an equal number of calendar days before and after each reference Diwali date. The festival day is reported separately. Each metric shows its own valid-day count; missing observations are never treated as zero. The summary compares measured mean values on Diwali day with the selected before window where both have data. It is descriptive and does not establish fireworks causality. 2026 is treated as upcoming until its date; future measurements are not accepted, and forecasts are not included in measured comparisons.

Coverage note: the [CPCB National AQI Dashboard](https://app.cpcbccr.com/AQIIndia) exposes current AQI monitoring information. The referenced [OGD Historical Daily Ambient Air Quality dataset](https://tn.data.gov.in/catalog/historical-daily-ambient-air-quality-data) describes SO₂, NO₂, RSPM and SPM and its catalog page lists a 2017 update; it does not establish PM₂.₅, PM₁₀ or AQI coverage for 2022–2026. RSPM/SPM are not assumed to be PM₁₀. CPCB publishes dated bulletins and repository material, but these do not by themselves establish a complete, consistent city/station/day series for every requested Diwali window. Import only source data you have verified, retain the source name and URL, and review each window’s missing-data and provenance notes.
