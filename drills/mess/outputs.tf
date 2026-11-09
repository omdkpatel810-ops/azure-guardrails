output "resource_group" {
  description = "The mess's resource group."
  value       = azurerm_resource_group.mess.name
}

output "resource_ids" {
  description = "Every resource the drill expects policy to flag."
  value = compact(concat(
    [azurerm_virtual_network.mess.id, azurerm_storage_account.exports.id, azurerm_mssql_server.poc.id, azurerm_mssql_database.poc.id],
    azurerm_network_interface.vm[*].id,
    azurerm_linux_virtual_machine.vm[*].id,
    azurerm_managed_disk.orphan[*].id,
    azurerm_public_ip.orphan[*].id,
    azurerm_service_plan.empty[*].id,
  ))
}
