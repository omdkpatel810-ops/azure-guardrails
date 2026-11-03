#!/usr/bin/env bash
# One-time setup, run by the subscription's owner in Azure Cloud Shell (Bash):
#
#   git clone https://github.com/omdkpatel810-ops/azure-guardrails.git
#   cd azure-guardrails
#   ./bootstrap/bootstrap.sh online
#
# The argument is the management group this subscription goes in: platform, corp, online,
# sandbox or decommissioned (default online). It's safe to run again; finished steps are skipped.
#
# What it does, and why a person runs it rather than the pipeline:
#   1. Creates the Guardrails root management group. Creating groups under the tenant root and
#      moving subscriptions need rights the pipeline deliberately never gets.
#   2. Applies bootstrap/: state storage, audit logging, and the plan and apply identities that
#      GitHub Actions signs in as with OpenID Connect (no secrets anywhere).
#   3. Moves bootstrap's own state into that storage.
#   4. Applies platform/ once: the hierarchy, the policies, and this subscription's placement.
#   5. Sets the repository variables and the production environment the deploy workflow uses,
#      if the GitHub CLI is signed in. Otherwise it prints them.
# After this, every change goes through a pull request.

set -euo pipefail

PLACEMENT="${1:-online}"
PREFIX="${PREFIX:-guardrails}"
LOCATION="${LOCATION:-canadacentral}"
REPO="${REPO:-omdkpatel810-ops/azure-guardrails}"
STATE_RG="rg-guardrails-tfstate"
CONTAINER="tfstate"

cd "$(dirname "$0")/.."
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

case "$PLACEMENT" in
  platform | corp | online | sandbox | decommissioned) ;;
  *) fail "Place the subscription in platform, corp, online, sandbox or decommissioned, not '$PLACEMENT'." ;;
esac
for tool in az terraform jq sha1sum; do
  command -v "$tool" >/dev/null || fail "$tool is missing. Azure Cloud Shell has all of these."
done

step "Checking the signed-in account"
SUB=$(az account show --query id -o tsv) || fail "Sign in first: az login"
TENANT=$(az account show --query tenantId -o tsv)
echo "Subscription: $(az account show --query name -o tsv) ($SUB)"
echo "Tenant:       $TENANT"
export ARM_SUBSCRIPTION_ID="$SUB" ARM_TENANT_ID="$TENANT"

step "Checking that $LOCATION is allowed"
# Some offers, Azure for Students among them, limit which regions a subscription may use.
limits=$(az policy assignment list --scope "/subscriptions/$SUB" --disable-scope-strict-match \
  --query "[?parameters.listOfAllowedLocations].parameters.listOfAllowedLocations.value[]" -o tsv 2>/dev/null || true)
if [[ -n "$limits" ]] && ! grep -qx "$LOCATION" <<<"$limits"; then
  fail "This subscription only allows these regions:
  ${limits//$'\n'/$'\n'  }
Pick one: LOCATION=<region> ./bootstrap/bootstrap.sh $PLACEMENT
Then set allowed_locations in platform/guardrails.auto.tfvars to regions from that list."
fi
echo "OK"

step "Registering the resource providers policy compliance and state need"
for ns in Microsoft.PolicyInsights Microsoft.Storage Microsoft.OperationalInsights Microsoft.Insights Microsoft.Management; do
  az provider register --namespace "$ns" --wait >/dev/null && echo "  $ns"
done

step "Creating the $PREFIX root management group"
if az account management-group show --name "$PREFIX" >/dev/null 2>&1; then
  echo "Already there."
else
  az account management-group create --name "$PREFIX" --display-name "Guardrails" >/dev/null
  echo "Created. You're its Owner, as its creator."
fi

ACCOUNT="stgrstate$(printf '%s' "$SUB" | sha1sum | cut -c1-10)"
BACKEND=(
  -backend-config="resource_group_name=$STATE_RG"
  -backend-config="storage_account_name=$ACCOUNT"
  -backend-config="container_name=$CONTAINER"
  -backend-config="use_azuread_auth=true"
)
TFVARS=(-var "location=$LOCATION" -var "github_repository=$REPO" -var "root_management_group=$PREFIX")
printf 'terraform {\n  backend "azurerm" {}\n}\n' >bootstrap/backend_override.tf.next

step "Applying bootstrap/ (state storage, audit log, pipeline identities)"
if az storage blob exists --account-name "$ACCOUNT" --container-name "$CONTAINER" --name bootstrap.tfstate \
  --auth-mode login --query exists -o tsv 2>/dev/null | grep -q true; then
  echo "Bootstrap state is already in $ACCOUNT; using it."
  mv bootstrap/backend_override.tf.next bootstrap/backend_override.tf
  terraform -chdir=bootstrap init -input=false -reconfigure "${BACKEND[@]}" -backend-config="key=bootstrap.tfstate"
  terraform -chdir=bootstrap apply "${TFVARS[@]}"
else
  rm -f bootstrap/backend_override.tf
  terraform -chdir=bootstrap init -input=false
  terraform -chdir=bootstrap apply "${TFVARS[@]}"

  step "Waiting for your access to the state storage (role assignments take a few minutes)"
  for _ in $(seq 1 40); do
    if az storage blob list --account-name "$ACCOUNT" --container-name "$CONTAINER" --auth-mode login -o none 2>/dev/null; then
      break
    fi
    sleep 15
  done
  az storage blob list --account-name "$ACCOUNT" --container-name "$CONTAINER" --auth-mode login -o none ||
    fail "Still no access to $ACCOUNT after 10 minutes. Wait a little and run this script again."

  step "Moving bootstrap's state into $ACCOUNT"
  mv bootstrap/backend_override.tf.next bootstrap/backend_override.tf
  terraform -chdir=bootstrap init -input=false -migrate-state -force-copy "${BACKEND[@]}" -backend-config="key=bootstrap.tfstate"
  rm -f bootstrap/terraform.tfstate bootstrap/terraform.tfstate.backup
fi
VARS=$(terraform -chdir=bootstrap output -json github_variables)

step "Applying platform/ (hierarchy, policies, and this subscription in $PLACEMENT)"
PLACEMENT_HCL="{\"$SUB\" = \"$PLACEMENT\"}"
terraform -chdir=platform init -input=false -reconfigure "${BACKEND[@]}"
if ! terraform -chdir=platform state show module.management_groups.azurerm_management_group.root >/dev/null 2>&1; then
  terraform -chdir=platform import -input=false -var "prefix=$PREFIX" \
    module.management_groups.azurerm_management_group.root "/providers/Microsoft.Management/managementGroups/$PREFIX"
fi
terraform -chdir=platform apply -var "prefix=$PREFIX" -var "subscription_placement=$PLACEMENT_HCL"

step "Connecting GitHub"
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  while IFS=$'\t' read -r name value; do
    gh variable set "$name" --repo "$REPO" --body "$value" && echo "  $name"
  done < <(jq -r 'to_entries[] | [.key, .value] | @tsv' <<<"$VARS")
  gh variable set SUBSCRIPTION_PLACEMENT --repo "$REPO" --body "$PLACEMENT_HCL" && echo "  SUBSCRIPTION_PLACEMENT"

  # Apply runs only from main, in an environment that waits for your approval.
  main_only='"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}'
  me=$(gh api user --jq .id)
  if ! gh api -X PUT "repos/$REPO/environments/production" --input - >/dev/null 2>&1 \
    <<<"{\"reviewers\": [{\"type\": \"User\", \"id\": $me}], $main_only}"; then
    echo "  Couldn't make you a required reviewer (private repos need a paid plan); main-only still applies."
    gh api -X PUT "repos/$REPO/environments/production" --input - >/dev/null <<<"{$main_only}"
  fi
  gh api -X POST "repos/$REPO/environments/production/deployment-branch-policies" -f name=main >/dev/null 2>&1 || true
  echo "  production environment: deploys from main only"
else
  echo "The GitHub CLI isn't signed in here, so add these yourself under the repository's"
  echo "Settings > Secrets and variables > Actions > Variables (none of them is a secret):"
  jq -r 'to_entries[] | "  \(.key) = \(.value)"' <<<"$VARS"
  echo "  SUBSCRIPTION_PLACEMENT = $PLACEMENT_HCL"
  echo "Then create an environment called production (Settings > Environments), limited to main."
fi

step "Done"
echo "Guardrails is live. From now on, change it with pull requests: they plan, main applies."
echo "Policy compliance results appear in the portal under Policy > Compliance within about 30 minutes."
