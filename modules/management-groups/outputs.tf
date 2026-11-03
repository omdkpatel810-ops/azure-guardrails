output "ids" {
  description = "Resource ID of each management group, by key: root, platform, landing-zones, corp, online, sandbox, decommissioned."
  value       = { for key, group in local.groups : key => group.id }
}

output "names" {
  description = "Name of each management group, by key."
  value       = { for key, group in local.groups : key => group.name }
}
