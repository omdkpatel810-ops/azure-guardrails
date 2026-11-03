output "definition_ids" {
  description = "Resource ID of each custom policy definition, by file name."
  value       = { for key, d in azurerm_policy_definition.this : key => d.id }
}

output "initiative_ids" {
  description = "Resource ID of each initiative, by file name."
  value       = { for key, i in azurerm_management_group_policy_set_definition.this : key => i.id }
}

output "assignment_ids" {
  description = "Resource ID of each assignment, by \"<group key>/<name>\"."
  value       = { for key, a in azurerm_management_group_policy_assignment.this : key => a.id }
}

output "remediation_principal_ids" {
  description = "Managed identity of each assignment that fixes resources, by \"<group key>/<name>\"."
  value       = { for key, a in azurerm_management_group_policy_assignment.this : key => a.identity[0].principal_id if length(a.identity) > 0 }
}
