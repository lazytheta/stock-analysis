#!/bin/bash
# Deploy LazyTheta MCP to Cloud Run. All flags in one execv call — no shell
# line continuations to corrupt the --set-secrets value.

set -e

PROJECT="stock-analysis-489016"
REGION="europe-west4"
SERVICE="lazytheta-mcp"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "Deploying $SERVICE to $REGION in project $PROJECT..."
echo "Source: $REPO_ROOT"
echo ""

gcloud run deploy "$SERVICE" \
    --project "$PROJECT" \
    --source . \
    --region "$REGION" \
    --platform managed \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --timeout 600 \
    --min-instances 0 \
    --max-instances 5 \
    --set-secrets="JWT_SIGNING_KEY=JWT_SIGNING_KEY:latest,SUPABASE_URL=SUPABASE_URL:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest"

echo ""
echo "Deploy complete. Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
