output "management_group_ids" {
  description = "Resource ID of each management group."
  value       = module.management_groups.ids
}

output "assignment_ids" {
  description = "Resource ID of each policy assignment."
  value       = module.policy.assignment_ids
}
