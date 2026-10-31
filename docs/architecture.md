# VAYURAKSHAK AI — Architecture

## Existing application

The project is one FastAPI application that serves a static browser UI and JSON endpoints. It has a shared SQLite community database, a pre-existing SQLite store for daily historical station observations, CSV storage for general imported observations, optional local ML artifacts, and an Open-Meteo client for the server-configured location.

No login or admin identity system existed in the citizen application. VAYU CAMPUS adds account sessions and an environment-configured administrator token without replacing citizen report IDs or the existing AQI/storage modules.

## System diagram

~~~mermaid
flowchart LR
  Resident[Citizen] --> UI[Existing static UI]
  Student[Student / college applicant] --> UI
  UI --> API[FastAPI application]
  API --> AQ[Open-Meteo current/forecast]
  AQ -->|Modelled PM2.5 / PM10| UI
  API --> Reports[(community.sqlite3: reports, groups)]
  API --> Campus[(community.sqlite3: campus users, reviews, campaigns, observations, audit)]
  API --> Hist[(historical.sqlite3: station-day observations)]
  API --> CSV[(observations.csv)]
  API --> Private[Private campus documents]
  CSV --> ML[Optional local model training/evaluation]
  Hist --> Compare[Diwali-aligned comparison]
  API -->|Authenticated with session cookie| Campus
  Admin[VAYURAKSHAK admin] -->|X-Admin-Token| API
  Campus -->|Approved records only| UI
  Compare --> UI
~~~

## Trust and approval boundaries

- Public discovery queries approved colleges, campaigns, and projects only.
- College/student applications, campaigns, project submissions, observations, and activity evidence remain private or unreviewed until explicit admin action.
- Campus login uses a salted scrypt password hash and an opaque, hashed session token in SQLite; the browser receives an HTTP-only, SameSite cookie.
- Admin-only routes compare the X-Admin-Token header with VAYU_ADMIN_TOKEN. Admin actions and private-document access are written to campus_audit.
- Verification and evidence documents live in backend/data/private-campus, which is not mounted as static content. Access is through an admin-protected file route.
- The existing VAYU CONNECT report flow remains pseudonymous. Self-reported confirmations are not verification. Only an administrator can transition submitted resolution evidence to verified resolved or closed without verification.
- Report and observation coordinates are rounded before storage and public responses.

## Campus workflows

~~~mermaid
flowchart TD
  ApplyCollege[Representative registers and submits college + private proof] --> PendingCollege[Pending application]
  PendingCollege -->|Admin review| ApprovedCollege[Approved public college]
  ApprovedCollege --> StudentApply[Student registers and requests membership + private proof]
  StudentApply -->|Admin review| VerifiedStudent[Verified student membership]
  VerifiedStudent --> Campaign[Campaign proposal]
  Campaign -->|Admin review| PublicCampaign[Approved inter-college campaign]
  PublicCampaign --> Join[Approved colleges join]
  Join --> Completion[Private activity proof submitted]
  Completion -->|Admin review| VerifiedActivity[Verified contribution count]
  VerifiedStudent --> Observation[Visual / sensor / external observation]
  Observation -->|Admin review| ReviewedDataset[Reviewed CSV export]
~~~

## Data integrity choices

- Existing community and historical databases are reused. There are no parallel reporting systems.
- SQLite uniqueness constraints prevent duplicate memberships, repeat campaign participation, and duplicate activity evidence by the same college. Observation review history and audit records are appended.
- Visual reports cannot include numeric PM readings. Sensor observations require a PM reading and instrument metadata; external measurements need a numeric reading and source. These remain observations, not automatically official data.
- CSV exports omit student identity, photo paths, and private documents, and include only reviewed records.
- The leaderboard exposes separate counts and ranks by admin-approved activity evidence, quality-check-passed observations, then approved campaigns. It does not estimate pollution reduction.
- Historical comparisons use actual Diwali dates and imported station-day records; incomplete years are labelled.
- AQI values are not calculated from PM2.5/PM10. Open-Meteo output is a modelled estimate, not a CPCB observation.

## Known boundaries

The administrator token is a single shared secret. Email verification, per-college admin delegation, official reporting-channel integrations, verified NGO discovery, street maps, multi-city live AQI requests, AQI standard normalization, scientific GLOBE protocol certification, and production operations are not configured. These are explicit integration requirements, not implied capabilities.
