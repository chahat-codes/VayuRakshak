# VAYURAKSHAK AI

A local-first air-quality and community action application with two community experiences:

- **VAYU CONNECT:** Residents share photo-first environmental reports, find community-submitted groups, respond to issues, and track follow-up. A report is “Verified resolved” only after administrator review.
- **VAYU CAMPUS:** College and student applications, approved college discovery, reviewed inter-college campaigns, structured environmental observations, research CSV export, project showcase, and contribution recognition.
- **AQI Prediction:** Source-labelled PM₂.₅/PM₁₀ estimates and forecasts. AQI is shown only when a supported source supplies it.
- **Diwali AQI Watch:** Compare imported station observations around each year’s actual Diwali date. Missing measurements remain missing.

## Run locally

~~~sh
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export VAYU_ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
~~~

Open <http://127.0.0.1:8001>. API docs are at <http://127.0.0.1:8001/docs>. Keep the admin token private and out of Git. Restart the server when changing it. For HTTPS deployments set VAYU_SECURE_COOKIE=1.

Air data defaults to Delhi. Configure the provider location before starting the server:

~~~sh
export VAYU_LATITUDE=28.6139
export VAYU_LONGITUDE=77.2090
export VAYU_LOCATION="Delhi"
~~~

The live PM data provider is Open-Meteo’s modelled grid, not a monitoring station. The default provider request is for the server-configured location. The app does not infer AQI from PM values.

## Campus verification and moderation

VAYU CAMPUS users register with an email and 12+ character password. College and student applications require a private verification document. College records, student membership, campaigns, campaign evidence, observations, and projects remain private/pending until review. Admin endpoints require the X-Admin-Token header, compared against VAYU_ADMIN_TOKEN. If it is unset, public campus browsing works but review actions return unavailable.

Private verification documents are stored under backend/data/private-campus/, outside the public /uploads mount. They are served only through admin-authenticated document routes. Campus sessions use HTTP-only, SameSite cookies and expire after seven days. Admin actions are recorded in the shared community SQLite audit table.

Approved students can propose campaigns, join approved campaigns, submit campaign evidence, and submit typed observations. Visual observations cannot contain PM sensor readings; sensor observations require instrument metadata. Observations are labelled unreviewed/reviewed/quality-checks-passed/rejected. “Quality checks passed” is an application review status, not scientific certification. Research export includes only reviewed records and omits identity and private files.

VAYU CONNECT retains the existing pseudonymous citizen-report flow; it is not authenticated. Citizen confirmations are self-reported. Report evidence awaits admin review and is not automatically treated as resolution.

## Existing data and APIs

The app reuses the existing community.sqlite3, historical.sqlite3, imported observation CSV, and optional model artifacts. Existing report records are retained. Legacy report states are migrated to under-review/action-reported/awaiting-verification statuses. Historical import, coverage and comparison routes, observation import/history/export routes, and model evaluation routes remain available.

Historical daily CSV format:

~~~csv
date,city,station,pm25,pm10,aqi
2024-10-31,Delhi,Station name,82.5,140,302
~~~

PM values are µg/m³; AQI is source-reported and unitless. Import validation rejects invalid/future rows and prevents station-day duplicates from overwriting prior readings. Source names and URLs are uploader-supplied, not independently verified.

## Important limitations

- No email verification provider is configured. Uploaded identity proof is reviewed by an administrator.
- VAYU_ADMIN_TOKEN is a single shared server secret, not a multi-user admin identity system.
- No official complaint submission or verified organisation directory is integrated.
- Student observations are not official measurements; no NASA/GLOBE affiliation or data transfer is claimed. GLOBE protocol compatibility has not yet been established.
- AQI/forecast comparisons are limited to the single server-configured provider location. Cross-campus model requests and AQI-standard normalization are not implemented.
- The report coordinate view is a local plot, not a street map. No third-party basemap receives coordinates.
- No production build step exists; the existing frontend is static HTML/CSS/JavaScript served by FastAPI.

## Tests

~~~sh
cd backend
python -m unittest discover -s tests -v
~~~
