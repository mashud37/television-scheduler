# TV Scheduler

A personal TV-programme monitoring service with adaptive machine-learning ranking, running on Google Cloud Platform.

## How it works

Every 12 days, Cloud Scheduler fires a job that:

1. Scrapes 14 days of prime-time listings from TVSpielfilm (sports + series, public TV)
2. Scores every show using pre-trained text models (title, description, cast, crew) plus your channel preferences and must-watch keywords
3. Sends you a **notification email** with a summary and a link to the selection page

You open the link in any browser, tick the shows you want to watch, and click **Save & send calendar**. The service then:

1. Emails you a **`.ics` calendar file** — import into Google Calendar, Outlook, or Apple Calendar
2. Saves your selections as labelled training data
3. **Automatically retrains** the ranking model so every subsequent run reflects your taste more precisely

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Google Cloud account with billing enabled | Billing must be active on the project |
| `gcloud` CLI | [Install guide](https://cloud.google.com/sdk/docs/install) |
| Python 3.10+ | Only needed if running locally |
| Gmail address + App Password | [Create one here](https://myaccount.google.com/apppasswords) — requires 2FA on the account |

---

## One-time setup

Run all commands from the VS Code terminal (PowerShell).

### Step 1 — Authenticate and create a project

```powershell
gcloud auth login
gcloud config set project YOUR_SHARED_PROJECT
```

Billing is managed through the [GCP Console](https://console.cloud.google.com/billing) — link the new project to your billing account there if it is not linked automatically.

### Step 2 — Enable required APIs

```powershell
gcloud services enable `
  run.googleapis.com `
  cloudscheduler.googleapis.com `
  storage.googleapis.com `
  artifactregistry.googleapis.com `
  cloudbuild.googleapis.com
```

### Step 3 — Create the GCS bucket

The bucket holds the SQLite database and the ranking model, mounted into Cloud Run at `/mnt/data`.

```powershell
$PROJECT = gcloud config get-value project
$BUCKET = "$PROJECT-tvsched-data"
$REGION = "europe-west1"

gcloud storage buckets create "gs://$BUCKET" `
  --location=$REGION `
  --uniform-bucket-level-access `
  --labels=app=tv-scheduler
```

### Step 4 — Upload the pre-trained model

```powershell
mkdir -Force assets/models
cp "C:/path/to/tv scheduler app/assets/models/tv_component_models.joblib" `
   assets/models/tv_component_models.joblib

gcloud storage cp assets/models/tv_component_models.joblib `
  "gs://$BUCKET/models/tv_component_models.joblib"
```

> **Shortcut:** `deploy.sh` in this directory uploads the model and automates Steps 3–9 in one shot — see the script header for usage (requires bash).

If you have no existing model the service still works — it ranks by channel preferences and must-watch keywords until you have made at least five selections.

### Step 5 — Generate secret tokens

Run this three times and save the output:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

| Token | Purpose |
|-------|---------|
| `SECRET_KEY` | Signs Flask session cookies |
| `ACCESS_TOKEN` | Appended to the selection-page link in every notification email |
| `JOB_TOKEN` | Authenticates Cloud Scheduler's HTTP call to `/internal/run-job` |

### Step 6 — Create env.yaml

```powershell
cp env.yaml.example env.yaml
```

Fill in all values:

```yaml
SMTP_HOST: "smtp.gmail.com"
SMTP_PORT: "587"
SMTP_USER: "your.address@gmail.com"
SMTP_PASS: "abcd efgh ijkl mnop"
EMAIL_TO:  "you@example.com"

BASE_URL: ""                              # fill in after first deploy (Step 7)

SECRET_KEY:   "paste-first-token-here"
ACCESS_TOKEN: "paste-second-token-here"
JOB_TOKEN:    "paste-third-token-here"

DB_PATH:     "/mnt/data/tv_scheduler.db"
MODEL_PATH:  "/mnt/data/models/tv_component_models.joblib"
CONFIG_PATH: "/mnt/data/ranker_config.json"

HTTPS: "true"
```

**Never commit `env.yaml` to git.** It is in `.gitignore`.

### Step 7 — Deploy to Cloud Run

`--source .` builds a container image via Cloud Build and deploys it in one step. The Artifact Registry repository is created automatically.

```powershell
gcloud run deploy tvsched `
  --source . `
  --region $REGION `
  --env-vars-file env.yaml `
  --add-volume "name=data,type=cloud-storage,bucket=$BUCKET" `
  --add-volume-mount "volume=data,mount-path=/mnt/data" `
  --memory 2Gi `
  --cpu 2 `
  --timeout 1800 `
  --max-instances 1 `
  --allow-unauthenticated `
  --labels app=tv-scheduler
```

When the command finishes it prints a **Service URL** that looks like `https://tvsched-HASH-ew.a.run.app`.

### Step 8 — Set BASE_URL and redeploy

Copy the Service URL from Step 7 into `env.yaml`:

```yaml
BASE_URL: "https://tvsched-HASH-ew.a.run.app"
```

Then redeploy to apply it:

```powershell
gcloud run services update tvsched `
  --region $REGION `
  --env-vars-file env.yaml
```

### Step 9 — Create the Cloud Scheduler job

```powershell
$SERVICE_URL = gcloud run services describe tvsched `
  --region $REGION `
  --format="value(status.url)"

gcloud scheduler jobs create http tvsched-weekly-sched `
  --location $REGION `
  --schedule "0 7 1,13,25 * *" `
  --uri "$SERVICE_URL/internal/run-job?token=YOUR_JOB_TOKEN" `
  --http-method GET `
  --attempt-deadline 1800s `
  --time-zone "Europe/Berlin"
```

Replace `YOUR_JOB_TOKEN` with the `JOB_TOKEN` value from `env.yaml`.

> The schedule `0 7 1,13,25 * *` fires on the 1st, 13th, and 25th of each month at 07:00 Berlin time. Adjust to your preference.

### Step 10 — Verify

```powershell
# Health check
Invoke-WebRequest "$SERVICE_URL/healthz"

# Trigger a manual run
gcloud scheduler jobs run tvsched-weekly-sched --location $REGION

# Check logs
gcloud logging read `
  "resource.type=cloud_run_revision AND resource.labels.service_name=tvsched" `
  --limit 50 `
  --format "table(timestamp, textPayload)"
```

---

## Day-to-day use

1. You receive an email: **"TV Guide — 2026-05-09 (147)"**
2. Click the link — a ranked schedule grid opens in your browser
3. Tick the shows you want to watch (must-watch highlights are marked in blue)
4. Click **Save & send calendar**
5. You receive a second email with `tv_schedule_2026-05-09.ics` attached
6. Import the `.ics` into your calendar

The model retrains immediately after each selection. After a few cycles it learns your preferences precisely.

---

## Customising the ranking

User preferences live in a hand-editable YAML file. Out of the box the code ships with **empty personal defaults** — no embedded channel preferences or must-watch lists — and falls back to the committed [`ranker_prefs.example.yaml`](ranker_prefs.example.yaml) so a fresh deploy is still functional.

### Editing in the web UI (easiest)

Open `/settings` in the deployed app. The page exposes:
- **Must-watch keywords** — one per line; matches are always ranked first
- **Channel preferences** — `Channel Name: weight` per line; negative values penalise, large negatives (e.g. `-100`) effectively hide a channel
- **Time slot boundaries** — three `HH:MM` values defining early / late / end of evening slots

Saves write back to the YAML prefs file atomically (temp file + rename).

### Editing the YAML directly

```powershell
# 1. start from the bundled example
Copy-Item ranker_prefs.example.yaml ranker_prefs.yaml

# 2. edit ranker_prefs.yaml in your editor of choice

# 3. upload to the mounted GCS path used by Cloud Run
gcloud storage cp ranker_prefs.yaml "gs://$BUCKET/ranker_prefs.yaml"
```

Changes take effect on the next scheduled run — no redeployment needed.

**Note**: `ranker_prefs.yaml` is in `.gitignore` so your personal preferences never end up in source control. The `.example.yaml` template IS committed and serves both as documentation and as the bootstrap fallback.

### Path resolution

The ranker checks, in order:
1. `$RANKER_PREFS_PATH` (default: `/mnt/data/ranker_prefs.yaml`)
2. `$CONFIG_PATH` (legacy — used to point at a JSON file; still accepted, parser chosen by extension)
3. `ranker_prefs.example.yaml` bundled in the image
4. Empty defaults (no channel weights, no must-watch list)

| Field | Effect |
|-------|--------|
| `channel_prior` | Positive values boost a channel; `-100` effectively hides it |
| `default_channel_score` | Score used when a channel isn't listed |
| `must_watch_keywords` | Shows whose title matches are always ranked first |
| `component_weights` | How much each text field contributes to the final score |
| `early_start_min` / `late_start_min` / `late_end_min` | Slot boundaries (minutes since midnight) |

---

## Importing historical training data

If you have training CSVs from the desktop app, seed the model before the first run:

```powershell
python scripts/import_historical.py `
  --csv-dir "C:/path/to/tv scheduler app/csv" `
  --db-path data/tv_scheduler.db

python scripts/retrain.py `
  --db-path data/tv_scheduler.db `
  --out assets/models/tv_component_models.joblib `
  --upload "gs://${PROJECT}-tvsched-data/models/tv_component_models.joblib"
```

---

## Running locally (for testing)

Create a `.env` file with the same keys as `env.yaml.example` (one `KEY=value` per line), then:

```powershell
pip install -r requirements.txt

python scripts/run_job_local.py
python scripts/run_job_local.py --skip-email
python scripts/run_job_local.py --run-date 2026-05-09 --skip-email
```

---

## Updating the deployment

After changing any source file:

```powershell
gcloud run deploy tvsched `
  --source . `
  --region $REGION `
  --env-vars-file env.yaml `
  --add-volume "name=data,type=cloud-storage,bucket=$BUCKET" `
  --add-volume-mount "volume=data,mount-path=/mnt/data"
```

To update only environment variables (no new build):

```powershell
gcloud run services update tvsched `
  --region $REGION `
  --env-vars-file env.yaml
```

---

## Architecture

```
Cloud Scheduler (every ~12 days, 07:00 Berlin)
    |
    +-- GET /internal/run-job?token=JOB_TOKEN
            |
            +-- retrain model (if >= 5 selections accumulated)
            +-- scrape TVSpielfilm  (14-day prime-time window)
            +-- score_shows()       (channel + text models + keywords)
            +-- save to SQLite (GCS mount)
            +-- send notification email
                    |
                    +-- User clicks link --> /run/<date>?token=ACCESS_TOKEN
                                |
                                +-- schedule grid (grouped by date / slot)
                                        |
                                        +-- POST /run/<date>/select
                                                |
                                                +-- save selections + training data
                                                +-- build .ics calendar
                                                +-- send selection email (.ics attached)
                                                +-- retrain model immediately
```

### Data persistence (GCS bucket)

| GCS path | Contents |
|----------|---------|
| `models/tv_component_models.joblib` | Current ranking model — overwritten after each retraining |
| `tv_scheduler.db` | SQLite: shows, scores, selections, training data |
| `ranker_config.json` | Optional custom channel/keyword/weight config |

### Source files

| File | Purpose |
|------|---------|
| `src/app.py` | Flask web app — selection grid, job trigger endpoint |
| `src/db.py` | SQLite schema and all data-access functions |
| `src/scraper.py` | TVSpielfilm HTML scraper (listing pages + detail pages) |
| `src/ranker.py` | `RankerConfig`, model loading, `score_shows()` |
| `src/trainer.py` | Auto-retraining: Ridge regression + TF-IDF, one model per text field |
| `src/emailer.py` | Notification email (with link) + selection email (`.ics` attachment) |
| `src/scheduler_job.py` | Orchestrator called by the job trigger route |
| `scripts/import_historical.py` | Seed training table from desktop-app CSV exports |
| `scripts/retrain.py` | Retrain model locally and optionally upload to GCS |
| `scripts/run_job_local.py` | Run one full job cycle locally for testing |

---

## Troubleshooting

**The Scheduler job times out**  
Scraping 200+ detail pages takes 10–30 minutes. The Cloud Run timeout is set to 1800 s (30 min) and Scheduler's `--attempt-deadline` matches. If it still times out, reduce the scope by editing `DEFAULT_FIXED_QS` in `scraper.py` (e.g. remove the `SP` sports category).

**"No model found" in the logs**  
The GCS model path is empty. Run Step 4 to upload the model, or make at least five selections via the web UI to trigger an automatic retrain.

**Selection email arrives but .ics is empty**  
The date/time parser in `_build_ics()` could not parse a show's time string. The `time` column must be in `HH:MM-HH:MM` format.

**"Retraining skipped: only N selected shows"**  
The model retrains once you have at least five selections across all runs. Use `scripts/import_historical.py` to bootstrap from existing data.

**Cloud Run returns 401 on the selection page**  
The `ACCESS_TOKEN` in the email link does not match the one in `env.yaml`. Redeploy after updating `env.yaml`.
