# Environment vending: every request file in environments/ becomes an environment.
# tools/vending.py checks the requests first (unique names and networks, budgets, and that the
# resource group passes the guardrails), so this only has to build them.

locals {
  requests = {
    for file in fileset(var.requests_dir, "*.json") :
    trimsuffix(file, ".json") => merge(jsondecode(file("${var.requests_dir}/${file}")), { file = file })
  }

  groups = toset(flatten([
    for r in values(local.requests) : concat(try(r.access.contributors, []), try(r.access.readers, []))
  ]))
}

# Requests name groups, not object IDs, so they're readable in review. The pipeline identities can
# read groups (Group.Read.All), nothing more, in Microsoft Graph.
data "azuread_group" "team" {
  for_each = local.groups

  display_name     = each.key
  security_enabled = true
}

module "environment" {
  source   = "../modules/environment"
  for_each = local.requests

  team           = each.value.team
  environment    = each.value.environment
  owner          = each.value.owner
  cost_center    = each.value.cost_center
  location       = try(each.value.region, "canadacentral")
  monthly_budget = each.value.monthly_budget
  address_space  = try(each.value.address_space, null)
  request_file   = each.value.file

  contributor_groups = { for g in try(each.value.access.contributors, []) : g => data.azuread_group.team[g].object_id }
  reader_groups      = { for g in try(each.value.access.readers, []) : g => data.azuread_group.team[g].object_id }
}
