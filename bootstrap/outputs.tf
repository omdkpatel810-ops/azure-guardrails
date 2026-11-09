output "github_variables" {
  description = "Repository variables the deploy workflow reads. None of them is a secret."
  value = {
    AZURE_TENANT_ID         = data.azurerm_client_config.current.tenant_id
    AZURE_SUBSCRIPTION_ID   = data.azurerm_client_config.current.subscription_id
    AZURE_PLAN_CLIENT_ID    = azuread_application.pipeline["plan"].client_id
    AZURE_APPLY_CLIENT_ID   = azuread_application.pipeline["apply"].client_id
    TFSTATE_RESOURCE_GROUP  = azurerm_resource_group.state.name
    TFSTATE_STORAGE_ACCOUNT = azurerm_storage_account.state.name
    TFSTATE_CONTAINER       = azurerm_storage_container.state.name
    COST_EXPORT_ACCOUNT     = azurerm_storage_account.costs.name
    COST_EXPORT_CONTAINER   = azurerm_storage_container.costs.name
  }
}
