output "environments" {
  description = "Each environment's resource group, by request name."
  value       = { for name, e in module.environment : name => e.resource_group_name }
}
