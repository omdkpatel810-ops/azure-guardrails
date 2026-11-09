output "resource_group_id" {
  description = "Resource ID of the environment's resource group."
  value       = azurerm_resource_group.this.id
}

output "resource_group_name" {
  description = "Name of the environment's resource group."
  value       = azurerm_resource_group.this.name
}

output "subnet_id" {
  description = "Resource ID of the workloads subnet, or null without a network."
  value       = one(azurerm_subnet.workloads[*].id)
}
