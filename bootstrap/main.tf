# What the pipeline needs before it can run: somewhere to keep Terraform state, and two
# identities GitHub Actions signs in as with OpenID Connect, so no secret is ever stored.
#
#   guardrails-plan   reads everything; used by pull requests and the nightly drift check
#   guardrails-apply  changes the hierarchy and policies; used only by the production environment

data "azurerm_client_config" "current" {}

data "azurerm_management_group" "root" {
  name = var.root_management_group
}

locals {
  github_issuer = "https://token.actions.githubusercontent.com"
  # Storage account names are global; derive a stable, unique suffix from the subscription.
  state_account = "stgrstate${substr(sha1(data.azurerm_client_config.current.subscription_id), 0, 10)}"
}

# --- State -------------------------------------------------------------------------------------

resource "azurerm_resource_group" "state" {
  name     = "rg-guardrails-tfstate"
  location = var.location
  tags     = var.tags
}

resource "azurerm_storage_account" "state" {
  #checkov:skip=CKV_AZURE_59:GitHub-hosted runners and Cloud Shell have no fixed addresses; access needs Entra ID sign-in and a data role, and shared keys are off.
  #checkov:skip=CKV_AZURE_33:Queue logging doesn't apply: this account holds only blobs.
  #checkov:skip=CKV2_AZURE_1:Microsoft-managed keys are enough for Terraform state; a customer-managed key needs a Key Vault, added in a later project.
  #checkov:skip=CKV2_AZURE_33:A private endpoint would need a self-hosted runner inside the network.
  #checkov:skip=CKV_AZURE_35:Same reason as CKV_AZURE_59: no fixed source addresses to allow.
  #checkov:skip=CKV_AZURE_43:The name is computed (stgrstate plus 10 hex characters), which Checkov can't resolve.

  name                     = local.state_account
  resource_group_name      = azurerm_resource_group.state.name
  location                 = azurerm_resource_group.state.location
  account_tier             = "Standard"
  account_replication_type = "GZRS" # three zones here, plus a copy in Canada East

  # The baseline policy's storage rules, applied to our own state first.
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  allow_nested_items_to_be_public   = false
  shared_access_key_enabled         = false # Entra ID sign-in only
  default_to_oauth_authentication   = true
  local_user_enabled                = false
  public_network_access_enabled     = true
  infrastructure_encryption_enabled = true
  cross_tenant_replication_enabled  = false

  sas_policy {
    expiration_period = "01.00:00:00"
  }

  blob_properties {
    versioning_enabled  = true # every state write can be rolled back
    change_feed_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  tags = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "state" {
  #checkov:skip=CKV2_AZURE_21:Reads are logged by the diagnostic setting below. The check only knows Storage Insights, which needs account keys, and keys are off.
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

# Every read and write of state is logged: state holds resource IDs and settings an attacker
# would want, so who read it matters. The daily cap keeps the cost to cents.
resource "azurerm_log_analytics_workspace" "audit" {
  name                = "log-guardrails-audit"
  resource_group_name = azurerm_resource_group.state.name
  location            = azurerm_resource_group.state.location
  sku                 = "PerGB2018"
  retention_in_days   = 90
  daily_quota_gb      = 0.05
  tags                = var.tags
}

resource "azurerm_monitor_diagnostic_setting" "state_blobs" {
  name                       = "audit-state-access"
  target_resource_id         = "${azurerm_storage_account.state.id}/blobServices/default"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.audit.id

  enabled_log {
    category = "StorageRead"
  }

  enabled_log {
    category = "StorageWrite"
  }

  enabled_log {
    category = "StorageDelete"
  }
}

resource "azurerm_management_lock" "state" {
  name       = "keep-terraform-state"
  scope      = azurerm_storage_account.state.id
  lock_level = "CanNotDelete"
  notes      = "Terraform state for Guardrails. Remove this lock only to retire the platform."
}

# --- Identities for GitHub Actions ---------------------------------------------------------------

resource "azuread_application" "pipeline" {
  for_each = toset(["plan", "apply"])

  display_name     = "guardrails-${each.key}"
  description      = "GitHub Actions in ${var.github_repository} (${each.key})."
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azurerm_client_config.current.object_id]
}

resource "azuread_service_principal" "pipeline" {
  for_each = azuread_application.pipeline

  client_id = each.value.client_id
  owners    = [data.azurerm_client_config.current.object_id]
}

# Which GitHub runs may sign in as which identity. The subject is set by GitHub and can't be
# chosen by the workflow: pull requests and main can plan; only the production environment,
# which needs approval, can apply.
locals {
  federated = {
    plan-pull-requests = { app = "plan", subject = "repo:${var.github_repository}:pull_request" }
    plan-main          = { app = "plan", subject = "repo:${var.github_repository}:ref:refs/heads/main" }
    apply-production   = { app = "apply", subject = "repo:${var.github_repository}:environment:production" }
  }
}

resource "azuread_application_federated_identity_credential" "github" {
  for_each = local.federated

  application_id = azuread_application.pipeline[each.value.app].id
  display_name   = each.key
  description    = "GitHub Actions: ${each.value.subject}"
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = local.github_issuer
  subject        = each.value.subject
}

# --- What each identity may do -------------------------------------------------------------------

locals {
  tag_contributor = "4a9ae827-6dc8-4573-8ac7-8239d42aa03f"

  # The apply identity may create role assignments only for Tag Contributor, the role the
  # tag-inheritance policies need, and only for service principals (the policies' identities).
  # So even a compromised pipeline can't make anyone Owner.
  only_tag_contributor = <<-EOT
    (
      (
        !(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})
      )
      OR
      (
        @Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.tag_contributor}}
        AND
        @Request[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {'ServicePrincipal'}
      )
    )
    AND
    (
      (
        !(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})
      )
      OR
      (
        @Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.tag_contributor}}
      )
    )
  EOT

  grants = {
    apply-management-groups = { who = "apply", role = "Management Group Contributor", scope = data.azurerm_management_group.root.id }
    apply-policy            = { who = "apply", role = "Resource Policy Contributor", scope = data.azurerm_management_group.root.id }
    apply-role-assignments  = { who = "apply", role = "Role Based Access Control Administrator", scope = data.azurerm_management_group.root.id, condition = local.only_tag_contributor }
    apply-state             = { who = "apply", role = "Storage Blob Data Contributor", scope = azurerm_storage_container.state.id }
    plan-read               = { who = "plan", role = "Reader", scope = data.azurerm_management_group.root.id }
    plan-state              = { who = "plan", role = "Storage Blob Data Reader", scope = azurerm_storage_container.state.id }
  }
}

resource "azurerm_role_assignment" "pipeline" {
  for_each = local.grants

  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azuread_service_principal.pipeline[each.value.who].object_id
  principal_type       = "ServicePrincipal"
  condition            = lookup(each.value, "condition", null)
  condition_version    = lookup(each.value, "condition", null) == null ? null : "2.0"
  description          = "guardrails-${each.value.who}: ${each.key}"
}

# The person running bootstrap also needs to read and write state: Owner covers the
# subscription's settings but not the data inside storage.
resource "azurerm_role_assignment" "admin_state" {
  scope                = azurerm_storage_container.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
  description          = "Bootstrap admin: read and write Terraform state."
}
