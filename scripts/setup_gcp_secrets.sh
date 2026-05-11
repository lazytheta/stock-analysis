#!/bin/bash
# One-shot setup of the 4 GCP secrets for the LazyTheta MCP Cloud Run deploy.
# Reads Supabase keys from local files; generates a fresh JWT signing key.
# Idempotent-ish: skips secrets that already exist with a version, adds versions
# to empty ones, creates missing ones.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ---- Read values from local files ----

CLAUDE_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
SUPABASE_URL=$(python3 -c "import json; print(json.load(open('$CLAUDE_CONFIG'))['mcpServers']['lazytheta-dcf']['env']['SUPABASE_URL'], end='')")
SUPABASE_SERVICE_KEY=$(python3 -c "import json; print(json.load(open('$CLAUDE_CONFIG'))['mcpServers']['lazytheta-dcf']['env']['SUPABASE_SERVICE_KEY'], end='')")
SUPABASE_ANON_KEY=$(python3 -c "import re; m=re.search(r'SUPABASE_ANON_KEY\s*=\s*\"([^\"]+)\"', open('.streamlit/secrets.toml').read()); print(m.group(1), end='')")
JWT_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')")

echo "Read SUPABASE_URL ($(echo -n $SUPABASE_URL | wc -c | tr -d ' ') chars)"
echo "Read SUPABASE_SERVICE_KEY ($(echo -n $SUPABASE_SERVICE_KEY | wc -c | tr -d ' ') chars)"
echo "Read SUPABASE_ANON_KEY ($(echo -n $SUPABASE_ANON_KEY | wc -c | tr -d ' ') chars)"
echo "Generated JWT_SIGNING_KEY ($(echo -n $JWT_KEY | wc -c | tr -d ' ') chars)"
echo ""

# ---- Helper: create-or-add-version ----

create_or_update_secret() {
    local name="$1"
    local value="$2"

    if gcloud secrets describe "$name" >/dev/null 2>&1; then
        echo "Secret $name exists — adding new version..."
        echo -n "$value" | gcloud secrets versions add "$name" --data-file=-
    else
        echo "Creating $name..."
        echo -n "$value" | gcloud secrets create "$name" --data-file=-
    fi
}

# ---- Create / update each ----

create_or_update_secret JWT_SIGNING_KEY "$JWT_KEY"
create_or_update_secret SUPABASE_URL "$SUPABASE_URL"
create_or_update_secret SUPABASE_SERVICE_KEY "$SUPABASE_SERVICE_KEY"
create_or_update_secret SUPABASE_ANON_KEY "$SUPABASE_ANON_KEY"

echo ""
echo "Done. Verifying:"
gcloud secrets list
