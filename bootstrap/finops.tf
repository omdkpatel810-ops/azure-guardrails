# FinOps foundations, applied once by an admin like the rest of bootstrap:
#   - a daily export of this month's actual costs to storage, which the FinOps report reads
#   - an email when Cost Management sees spending that doesn't fit the usual pattern
#   - the subscription's activity log in the audit workspace: who changed what, and when

resource "azurerm_resource_group" "finops" {
  name     = "rg-guardrails-finops"
  location = var.location
  tags     = var.tags
}

resource "azurerm_storage_account" "costs" {
  #checkov:skip=CKV_AZURE_59:Cost Management writes exports from Azure's network, and GitHub runners have no fixed addresses.
  #checkov:skip=CKV_AZURE_35:Same reason: no fixed source addresses to allow.
  #checkov:skip=CKV2_AZURE_33:A private endpoint would need a self-hosted runner inside the network.
  #checkov:skip=CKV2_AZURE_1:Microsoft-managed keys are enough for cost data; a customer-managed key needs a Key Vault, added in a later project.
  #checkov:skip=CKV_AZURE_33:Queue logging doesn't apply: this account holds only blobs.
  #checkov:skip=CKV_AZURE_43:The name is computed (stgrcost plus 10 hex characters), which Checkov can't resolve.
  #checkov:skip=CKV2_AZURE_40:Cost Management exports have needed account key access, so it stays on for this account only. People and pipelines read with Entra ID.
  #checkov:skip=CKV_AZURE_206:Exports can be regenerated from Cost Management, so copies in a second region would only add cost.
  name                              = "stgrcost${substr(sha1("costs-${data.azurerm_client_config.current.subscription_id}"), 0, 10)}"
  resource_group_name               = azurerm_resource_group.finops.name
  location                          = azurerm_resource_group.finops.location
  account_tier                      = "Standard"
  account_replication_type          = "LRS" # exports can be regenerated, so one region is enough
  shared_access_key_enabled         = true  # see CKV2_AZURE_40 above
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  allow_nested_items_to_be_public   = false
  default_to_oauth_authentication   = true
  local_user_enabled                = false
  infrastructure_encryption_enabled = true
  cross_tenant_replication_enabled  = false
  tags                              = var.tags

  sas_policy {
    expiration_period = "01.00:00:00"
  }

  blob_properties {
    delete_retention_policy {
      days = 7
    }

    container_delete_retention_policy {
      days = 7
    }
  }

  lifecycle {
    prevent_destroy = true # 13 months of history, for year-over-year comparisons
  }
}

resource "azurerm_storage_container" "costs" {
  #checkov:skip=CKV2_AZURE_21:Cost exports are regenerated daily and hold no secrets; reads are by the plan identity only.
  name                  = "cost-exports"
  storage_account_id    = azurerm_storage_account.costs.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

# Exports pile up daily; after 13 months they're no longer needed for year-over-year comparisons.
resource "azurerm_storage_management_policy" "costs" {
  storage_account_id = azurerm_storage_account.costs.id

  rule {
    name    = "expire-old-exports"
    enabled = true
    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["cost-exports/"]
    }
    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = 30
        delete_after_days_since_modification_greater_than       = 395
      }
    }
  }
}

resource "azurerm_subscription_cost_management_export" "daily" {
  name                         = "guardrails-daily-actual-cost"
  subscription_id              = "/subscriptions/${data.azurerm_client_config.current.subscription_id}"
  recurrence_type              = "Daily"
  recurrence_period_start_date = formatdate("YYYY-MM-DD'T'00:00:00Z", timeadd(plantimestamp(), "24h"))
  recurrence_period_end_date   = formatdate("YYYY-MM-DD'T'00:00:00Z", timeadd(plantimestamp(), "43800h")) # five years
  file_format                  = "Csv"

  export_data_storage_location {
    container_id     = azurerm_storage_container.costs.id
    root_folder_path = "daily"
  }

  export_data_options {
    type       = "ActualCost"
    time_frame = "MonthToDate"
  }

  lifecycle {
    ignore_changes = [recurrence_period_start_date, recurrence_period_end_date]
  }
}

resource "azurerm_cost_anomaly_alert" "subscription" {
  name            = "guardrails-anomalies"
  display_name    = "Guardrails: unusual spending"
  subscription_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}"
  email_subject   = "Unusual Azure spending in the Guardrails subscription"
  email_addresses = [var.finops_email]
  message         = "Cost Management found spending that doesn't fit the usual daily pattern. Check the FinOps report for which team and resource."
}

# The activity log costs nothing to keep in Log Analytics for 90 days.
resource "azurerm_monitor_diagnostic_setting" "activity_log" {
  name                       = "guardrails-activity-log"
  target_resource_id         = "/subscriptions/${data.azurerm_client_config.current.subscription_id}"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.audit.id

  dynamic "enabled_log" {
    for_each = ["Administrative", "Security", "Policy", "Alert", "Recommendation", "ServiceHealth", "ResourceHealth"]
    content {
      category = enabled_log.value
    }
  }
}
