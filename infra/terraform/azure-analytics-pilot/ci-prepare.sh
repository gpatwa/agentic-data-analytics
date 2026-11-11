#!/usr/bin/env bash
# Inputs the workflow derives at run time:
#  - the image tag currently deployed (an infrastructure run never changes what the deploy workflow shipped);
#  - the four secrets the stack takes as variables, read from the stack's own Key Vault with the workflow's OIDC identity.
#    They are masked before they reach the environment, so they never appear in these (public) logs, and nothing is rotated:
#    the values are exactly the live ones.
set -euo pipefail
rg="${PILOT_RESOURCE_GROUP:?PILOT_RESOURCE_GROUP is required}"
kv="${PILOT_KEY_VAULT:?PILOT_KEY_VAULT is required}"

tag="$(az containerapp show -g "$rg" -n compass-pilot-analytics-api --query "properties.template.containers[0].image" -o tsv | sed 's/.*://')"
echo "TF_VAR_image_tag=$tag" >> "$GITHUB_ENV"

secret() { az keyvault secret show --vault-name "$kv" --name "$1" --query value -o tsv; }
export_secret() {  # name value
  echo "::add-mask::$2"
  echo "TF_VAR_$1=$2" >> "$GITHUB_ENV"
}
# The two database passwords live inside the connection URLs the stack stored; take them back out.
password_of() { python3 -c 'import sys, urllib.parse as u; print(u.unquote(u.urlsplit(sys.argv[1]).password or ""))' "$1"; }

export_secret api_key "$(secret analytics-api-key)"
export_secret run_id_key "$(secret analytics-run-id-key)"
export_secret postgres_admin_password "$(password_of "$(secret analytics-control-db-url)")"
export_secret postgres_readonly_password "$(password_of "$(secret analytics-db-url)")"
