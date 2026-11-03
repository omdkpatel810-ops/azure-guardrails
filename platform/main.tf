module "management_groups" {
  source = "../modules/management-groups"

  prefix                 = var.prefix
  subscription_placement = var.subscription_placement
}

module "policy" {
  source = "../modules/policy"

  policy_dir           = "${path.module}/../policies"
  management_group_ids = module.management_groups.ids
  allowed_locations    = var.allowed_locations
  identity_location    = var.allowed_locations[0]
  exemptions           = var.exemptions
}
