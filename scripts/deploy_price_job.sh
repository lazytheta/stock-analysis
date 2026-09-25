#!/bin/bash
# Bouw de price-history-job, zet hem op Cloud Run en plan hem dagelijks.
# Herhaalbaar: elke stap maakt aan of werkt bij.

set -e

PROJECT="stock-analysis-489016"
PROJECT_NUMBER="93552884631"
REGION="europe-west4"
JOB="price-history"
IMAGE="europe-west4-docker.pkg.dev/$PROJECT/cloud-run-source-deploy/$JOB:latest"
SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
SCHEDULE="30 23 * * 1-5"     # ma–vr 23:30 Amsterdam, na de slotkoers in de VS
RUN_URI="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT/jobs/$JOB:run"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "== build $IMAGE"
gcloud builds submit --project "$PROJECT" --config cloudbuild.prices.yaml .

echo "== job $JOB"
gcloud run jobs deploy "$JOB" \
    --project "$PROJECT" \
    --region "$REGION" \
    --image "$IMAGE" \
    --memory 512Mi \
    --cpu 1 \
    --task-timeout 3600 \
    --max-retries 1 \
    --set-secrets="SUPABASE_URL=SUPABASE_URL:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest"

echo "== scheduler mag de job starten"
gcloud run jobs add-iam-policy-binding "$JOB" \
    --project "$PROJECT" --region "$REGION" \
    --member "serviceAccount:$SA" --role roles/run.invoker >/dev/null

echo "== dagelijkse trigger"
if gcloud scheduler jobs describe "$JOB-daily" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
    ACTION=update
else
    ACTION=create
fi
gcloud scheduler jobs $ACTION http "$JOB-daily" \
    --project "$PROJECT" \
    --location "$REGION" \
    --schedule "$SCHEDULE" \
    --time-zone "Europe/Amsterdam" \
    --uri "$RUN_URI" \
    --http-method POST \
    --oauth-service-account-email "$SA"

echo ""
echo "Klaar. Handmatig draaien: gcloud run jobs execute $JOB --region $REGION"
