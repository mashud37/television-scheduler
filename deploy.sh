#!/usr/bin/env bash
# deploy.sh — full one-shot deployment for television-scheduler
# Usage: edit the variables below, then: bash deploy.sh
#
# NOTE: gcloud_app.yaml is the authoritative config (see ../GCLOUD_POLICY.md and
#       ../manage.py). This script is the standalone fallback — keep it in sync.
#
# First-time flow:
#   1. Set PROJECT, REGION, and leave JOB_TOKEN empty.
#   2. Run: bash deploy.sh  — this builds and deploys the service.
#   3. Copy the printed SERVICE_URL into env.yaml as BASE_URL.
#   4. Set JOB_TOKEN to the value from env.yaml.
#   5. Run: bash deploy.sh  — redeploys with BASE_URL and creates the scheduler.

set -euo pipefail

PROJECT=your-gcp-project
REGION=europe-west1
SERVICE_NAME=tvsched
BUCKET="${PROJECT}-tvsched-data"
SA="tvsched-sa@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/tvsched/app:latest"

# Paste your JOB_TOKEN from env.yaml here before the second run
JOB_TOKEN=""

# ── enable APIs ───────────────────────────────────────────────────────────────
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --project="${PROJECT}"

# ── Artifact Registry repo ────────────────────────────────────────────────────
gcloud artifacts repositories create tvsched \
  --repository-format=docker \
  --location="${REGION}" \
  --project="${PROJECT}" 2>/dev/null || true

# ── data bucket ───────────────────────────────────────────────────────────────
gcloud storage buckets create "gs://${BUCKET}" \
  --location="${REGION}" \
  --uniform-bucket-level-access \
  --project="${PROJECT}" 2>/dev/null || echo "bucket ${BUCKET} already exists"
# `buckets create` has no --labels flag; labels are set via update.
gcloud storage buckets update "gs://${BUCKET}" \
  --update-labels=app=tv-scheduler --project="${PROJECT}"

# ── upload pre-trained model if available ─────────────────────────────────────
if [ -f "assets/models/tv_component_models.joblib" ]; then
  echo "Uploading pre-trained model to GCS..."
  gcloud storage cp assets/models/tv_component_models.joblib \
    "gs://${BUCKET}/models/tv_component_models.joblib"
fi

# ── service account for Cloud Scheduler ──────────────────────────────────────
gcloud iam service-accounts create tvsched-sa \
  --display-name="TV Scheduler — Cloud Scheduler invoker" \
  --project="${PROJECT}" 2>/dev/null || true

gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SA}" \
  --role=roles/run.invoker --condition=None

# The service RUNS AS this SA (see --service-account below), so it needs
# read/write access to the GCS bucket mounted at /mnt/data.
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" --role=roles/storage.objectAdmin

# ── build container image ─────────────────────────────────────────────────────
gcloud builds submit --tag "${IMAGE}" --project="${PROJECT}" .

# ── deploy Cloud Run service ──────────────────────────────────────────────────
gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --service-account="${SA}" \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=2 \
  --max-instances=1 \
  --timeout=1800 \
  --add-volume="name=data,type=cloud-storage,bucket=${BUCKET}" \
  --add-volume-mount="volume=data,mount-path=/mnt/data" \
  --env-vars-file=env.yaml \
  --labels=app=tv-scheduler \
  --project="${PROJECT}"

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region="${REGION}" \
  --format='value(status.url)' \
  --project="${PROJECT}")

# ── Cloud Scheduler trigger ───────────────────────────────────────────────────
if [ -n "${JOB_TOKEN}" ]; then
  gcloud scheduler jobs create http tvsched-weekly-sched \
    --location="${REGION}" \
    --schedule="0 7 1,13,25 * *" \
    --uri="${SERVICE_URL}/internal/run-job?token=${JOB_TOKEN}" \
    --http-method=GET \
    --time-zone="Europe/Berlin" \
    --attempt-deadline=1800s \
    --project="${PROJECT}" 2>/dev/null || \
  gcloud scheduler jobs update http tvsched-weekly-sched \
    --location="${REGION}" \
    --schedule="0 7 1,13,25 * *" \
    --uri="${SERVICE_URL}/internal/run-job?token=${JOB_TOKEN}" \
    --http-method=GET \
    --time-zone="Europe/Berlin" \
    --attempt-deadline=1800s \
    --project="${PROJECT}"
  echo "Scheduler job tvsched-weekly-sched created/updated."
else
  echo "JOB_TOKEN not set — skipping scheduler setup."
  echo "Set it at the top of this script and re-run to create the scheduler job."
fi

echo ""
echo "Deployment complete."
echo "Service URL: ${SERVICE_URL}"
echo ""
echo "Next steps (first deploy only):"
echo "  1. Paste '${SERVICE_URL}' as BASE_URL in env.yaml"
echo "  2. Set JOB_TOKEN at the top of this script"
echo "  3. Run: bash deploy.sh"
echo ""
echo "To trigger manually: gcloud scheduler jobs run tvsched-weekly-sched --location=${REGION}"
echo "To view logs:        gcloud run services logs tail ${SERVICE_NAME} --region=${REGION}"
