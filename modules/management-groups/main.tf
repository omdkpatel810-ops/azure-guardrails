# The hierarchy, following Microsoft's Cloud Adoption Framework landing zones:
#
#   <prefix>                      root: data residency for everything
#   ├── <prefix>-platform         shared services: networking, identity, logs
#   ├── <prefix>-landing-zones    application workloads: the baseline
#   │   ├── <prefix>-corp         internal apps: no public IPs
#   │   └── <prefix>-online       internet-facing apps
#   ├── <prefix>-sandbox          experiments: baseline in audit mode
#   └── <prefix>-decommissioned   retired subscriptions: clean-up only

resource "azurerm_management_group" "root" {
  name                       = var.prefix
  display_name               = "Guardrails"
  parent_management_group_id = var.parent_management_group_id
}

resource "azurerm_management_group" "top" {
  for_each = {
    platform       = "Platform"
    landing-zones  = "Landing zones"
    sandbox        = "Sandbox"
    decommissioned = "Decommissioned"
  }

  name                       = "${var.prefix}-${each.key}"
  display_name               = each.value
  parent_management_group_id = azurerm_management_group.root.id
}

resource "azurerm_management_group" "landing_zone" {
  for_each = {
    corp   = "Corp"
    online = "Online"
  }

  name                       = "${var.prefix}-${each.key}"
  display_name               = each.value
  parent_management_group_id = azurerm_management_group.top["landing-zones"].id
}

locals {
  groups = merge(
    { root = azurerm_management_group.root },
    azurerm_management_group.top,
    azurerm_management_group.landing_zone,
  )
}

resource "azurerm_management_group_subscription_association" "this" {
  for_each = var.subscription_placement

  management_group_id = local.groups[each.value].id
  subscription_id     = "/subscriptions/${each.key}"
}
