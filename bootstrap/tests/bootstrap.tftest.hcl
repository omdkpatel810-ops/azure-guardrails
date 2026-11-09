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

  mock_resource "azurerm_role_definition" {
    defaults = { role_definition_resource_id = "/providers/Microsoft.Management/managementGroups/guardrails/providers/Microsoft.Authorization/roleDefinitions/44444444-4444-4444-4444-444444444444" }
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

  mock_data "azuread_service_principal" {
    defaults = {
      object_id    = "00000000-0000-0000-0000-000000000999"
      app_role_ids = { "Group.Read.All" = "00000000-0000-0000-0000-000000000777" }
    }
  }
}

variables {
  finops_email = "finance@example.com"
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
      strcontains(azurerm_role_assignment.pipeline["apply-role-assignments"].condition, "GuidEquals {4a9ae827-6dc8-4573-8ac7-8239d42aa03f}\n    AND\n    @Request[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {'ServicePrincipal'}") &&
      strcontains(azurerm_role_assignment.pipeline["apply-role-assignments"].condition, "GuidEquals {b24988ac-6180-42a0-ab88-20f7382dd24c, acdd72a7-3385-48ef-bd42-f606fba81ae7}\n    AND\n    @Request[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {'Group'}") &&
      azurerm_role_assignment.pipeline["apply-role-assignments"].condition_version == "2.0"
    )
    error_message = "Tag Contributor only to service principals; Contributor and Reader only to groups."
  }

  assert {
    condition = !anytrue([
      for guid in ["8e3af657-a8ff-443c-a75c-2fe8c4bcb635", "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9", "f58310d9-a9f6-439a-9e8d-f62e7b41a168"] :
      strcontains(azurerm_role_assignment.pipeline["apply-role-assignments"].condition, guid)
    ])
    error_message = "Owner, User Access Administrator and RBAC Administrator can never be assigned."
  }

  assert {
    condition     = length([for g in values(azurerm_role_assignment.pipeline) : g if g.condition != null]) == 1
    error_message = "Only the role assignment grant needs a condition."
  }

  assert {
    condition     = !anytrue([for g in values(azurerm_role_assignment.pipeline) : contains(["Owner", "Contributor", "User Access Administrator"], coalesce(g.role_definition_name, "custom"))])
    error_message = "No identity gets Owner, Contributor or User Access Administrator."
  }
}

run "the_vending_role_creates_only_environments" {
  command = apply

  assert {
    condition     = azurerm_role_assignment.pipeline["apply-vending"].role_definition_id == azurerm_role_definition.vendor.role_definition_resource_id
    error_message = "The apply identity gets the custom vending role."
  }

  assert {
    condition = alltrue([
      for a in azurerm_role_definition.vendor.permissions[0].actions :
      !strcontains(a, "*") && anytrue([for p in ["Microsoft.Resources/subscriptions/resourceGroups/", "Microsoft.Network/virtualNetworks/", "Microsoft.Network/networkSecurityGroups/", "Microsoft.Consumption/budgets/", "Microsoft.Authorization/locks/"] : startswith(a, p)])
    ])
    error_message = "No wildcards, and nothing beyond resource groups, networks, budgets and locks: no VMs, databases or storage."
  }

  assert {
    condition     = azurerm_role_definition.vendor.assignable_scopes == tolist([data.azurerm_management_group.root.id])
    error_message = "The role can only be used inside Guardrails."
  }
}

run "teams_have_groups_and_pipelines_can_only_read_them" {
  command = apply

  assert {
    condition     = toset([for g in values(azuread_group.team) : g.display_name]) == toset(["grp-guardrails-payments", "grp-guardrails-storefront", "grp-guardrails-data", "grp-guardrails-support", "grp-guardrails-marketing", "grp-guardrails-finance"])
    error_message = "One security group per demo team, plus finance."
  }

  assert {
    condition     = alltrue([for g in values(azuread_group.team) : g.security_enabled])
    error_message = "Groups used for access are security groups."
  }

  assert {
    condition     = toset(keys(azuread_app_role_assignment.read_groups)) == toset(["plan", "apply"]) && alltrue([for a in values(azuread_app_role_assignment.read_groups) : a.app_role_id == "00000000-0000-0000-0000-000000000777"])
    error_message = "Both pipeline identities get Group.Read.All in Microsoft Graph, and only that."
  }
}

run "costs_are_exported_daily_and_watched" {
  command = apply

  assert {
    condition = (
      azurerm_subscription_cost_management_export.daily.recurrence_type == "Daily" &&
      azurerm_subscription_cost_management_export.daily.export_data_options[0].type == "ActualCost" &&
      azurerm_subscription_cost_management_export.daily.export_data_options[0].time_frame == "MonthToDate"
    )
    error_message = "This month's actual costs are exported every day."
  }

  assert {
    condition     = azurerm_subscription_cost_management_export.daily.export_data_storage_location[0].container_id == azurerm_storage_container.costs.id
    error_message = "Exports land in the cost-exports container."
  }

  assert {
    condition     = !azurerm_storage_account.costs.allow_nested_items_to_be_public && azurerm_storage_account.costs.min_tls_version == "TLS1_2" && azurerm_storage_container.costs.container_access_type == "private"
    error_message = "Cost data is never public."
  }

  assert {
    condition     = azurerm_role_assignment.pipeline["plan-cost-exports"].role_definition_name == "Storage Blob Data Reader" && azurerm_role_assignment.pipeline["plan-cost-exports"].scope == azurerm_storage_container.costs.id
    error_message = "The plan identity can read cost exports, and only read them."
  }

  assert {
    condition     = toset(azurerm_cost_anomaly_alert.subscription.email_addresses) == toset(["finance@example.com"])
    error_message = "Unusual spending is emailed to finops_email."
  }

  assert {
    condition     = toset([for l in azurerm_monitor_diagnostic_setting.activity_log.enabled_log : l.category]) == toset(["Administrative", "Security", "Policy", "Alert", "Recommendation", "ServiceHealth", "ResourceHealth"])
    error_message = "The activity log goes to the audit workspace."
  }
}

run "refuses_tags_the_baseline_would_deny" {
  command = plan

  variables {
    tags = { owner = "platform-team" }
  }

  expect_failures = [var.tags]
}
