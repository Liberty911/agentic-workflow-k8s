#!/bin/bash
# create-secret.sh
# Creates the orchestrator-secrets Kubernetes Secret directly from your
# local .env file — the key itself is never written into a YAML file
# that could accidentally get committed to git.

set -euo pipefail

ENV_FILE="../.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found. Run this script from helm/orchestrator/ or adjust ENV_FILE path."
  exit 1
fi

GROQ_API_KEY=$(grep GROQ_API_KEY "$ENV_FILE" | cut -d '=' -f2-)

if [ -z "$GROQ_API_KEY" ]; then
  echo "ERROR: GROQ_API_KEY not found in $ENV_FILE"
  exit 1
fi

kubectl create secret generic orchestrator-secrets \
  --from-literal=GROQ_API_KEY="$GROQ_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Secret 'orchestrator-secrets' created/updated."
