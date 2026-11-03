# Offline tests: both providers are mocked, so these need no Azure account.

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-00000000aaaa"
      subscription_id = "00000000-0000-0000-0000-00000000bbbb"
      object_id       = "00000000-0000-0000-0000-00000000cccc"
    }
  }

  mock_data "azurerm_management_group" {
    defaults = { id = "/providers/Microsoft.Management/managementGroups/guardrails" }
  }

  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-guardrails-tfstate" }
  }

  mock_resource "azurerm_storage_account" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-guardrails-tfstate/providers/Microsoft.Storage/storageAccounts/stgrstate" }
  }

  mock_resource "azurerm_storage_container" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-guardrails-tfstate/providers/Microsoft.Storage/storageAccounts/stgrstate/blobServices/default/containers/tfstate" }
  }

  mock_resource "azurerm_log_analytics_workspace" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-guardrails-tfstate/providers/Microsoft.OperationalInsights/workspaces/log-guardrails-audit" }
  }
}

mock_provider "azuread" {
  mock_resource "azuread_application" {
    defaults = {
      id        = "/applications/00000000-0000-0000-0000-00000000dddd"
      client_id = "00000000-0000-0000-0000-00000000eeee"
    }
  }

  mock_resource "azuread_service_principal" {
    defaults = { object_id = "00000000-0000-0000-0000-00000000ffff" }
  }
}

run "state_storage_follows_the_baseline" {
  command = apply

  assert {
    condition     = azurerm_storage_account.state.name == "stgrstate${substr(sha1("00000000-0000-0000-0000-00000000bbbb"), 0, 10)}" && length(azurerm_storage_account.state.name) <= 24
    error_message = "The account name should be stable per subscription and at most 24 characters."
  }

  assert {
    condition = (
      azurerm_storage_account.state.min_tls_version == "TLS1_2" &&
      azurerm_storage_account.state.https_traffic_only_enabled &&
      !azurerm_storage_account.state.allow_nested_items_to_be_public &&
      !azurerm_storage_account.state.shared_access_key_enabled
    )
    error_message = "State storage must pass the same storage rules the baseline enforces, with keys off."
  }

  assert {
    condition     = azurerm_storage_account.state.blob_properties[0].versioning_enabled && azurerm_storage_account.state.blob_properties[0].delete_retention_policy[0].days >= 30
    error_message = "State needs versioning and soft delete so a bad write can be undone."
  }

  assert {
    condition     = azurerm_management_lock.state.lock_level == "CanNotDelete"
    error_message = "State storage is locked against deletion."
  }

  assert {
    condition     = toset([for l in azurerm_monitor_diagnostic_setting.state_blobs.enabled_log : l.category]) == toset(["StorageRead", "StorageWrite", "StorageDelete"])
    error_message = "Every read, write and delete of state is logged."
  }

  assert {
    condition     = azurerm_resource_group.state.tags["owner"] != "" && azurerm_resource_group.state.tags["cost-center"] != "" && azurerm_resource_group.state.tags["environment"] != ""
    error_message = "The state resource group carries the tags the baseline requires."
  }
}

run "github_can_sign_in_only_from_the_right_places" {
  command = apply

  assert {
    condition = {
      for k, c in azuread_application_federated_identity_credential.github : k => c.subject
      } == {
      plan-pull-requests = "repo:omdkpatel810-ops/azure-guardrails:pull_request"
      plan-main          = "repo:omdkpatel810-ops/azure-guardrails:ref:refs/heads/main"
      apply-production   = "repo:omdkpatel810-ops/azure-guardrails:environment:production"
    }
    error_message = "Pull requests and main may plan; only the production environment may apply."
  }

  assert {
    condition     = alltrue([for c in values(azuread_application_federated_identity_credential.github) : c.issuer == "https://token.actions.githubusercontent.com" && c.audiences == tolist(["api://AzureADTokenExchange"])])
    error_message = "Tokens must come from GitHub Actions, for Entra ID."
  }
}

run "the_plan_identity_can_only_read" {
  command = apply

  assert {
    condition     = toset([for g in values(azurerm_role_assignment.pipeline) : g.role_definition_name if strcontains(g.description, "guardrails-plan:")]) == toset(["Reader", "Storage Blob Data Reader"])
    error_message = "The plan identity gets read-only roles."
  }
}

run "the_apply_identity_cant_hand_out_owner" {
  command = apply

  assert {
    condition     = azurerm_role_assignment.pipeline["apply-role-assignments"].role_definition_name == "Role Based Access Control Administrator"
    error_message = "Role assignments come from the RBAC Administrator role, which can't change anything else."
  }

  assert {
    condition = (
      strcontains(azurerm_role_assignment.pipeline["apply-role-assignments"].condition, "GuidEquals {4a9ae827-6dc8-4573-8ac7-8239d42aa03f}") &&
      strcontains(azurerm_role_assignment.pipeline["apply-role-assignments"].condition, "StringEqualsIgnoreCase {'ServicePrincipal'}") &&
      azurerm_role_assignment.pipeline["apply-role-assignments"].condition_version == "2.0"
    )
    error_message = "The apply identity may assign only Tag Contributor, and only to service principals."
  }

  assert {
    condition     = length([for g in values(azurerm_role_assignment.pipeline) : g if g.condition != null]) == 1
    error_message = "Only the role assignment grant needs a condition."
  }

  assert {
    condition     = !anytrue([for g in values(azurerm_role_assignment.pipeline) : contains(["Owner", "Contributor", "User Access Administrator"], g.role_definition_name)])
    error_message = "No identity gets Owner, Contributor or User Access Administrator."
  }
}

run "refuses_tags_the_baseline_would_deny" {
  command = plan

  variables {
    tags = { owner = "platform-team" }
  }

  expect_failures = [var.tags]
}
