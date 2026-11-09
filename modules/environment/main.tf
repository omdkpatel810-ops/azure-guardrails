# One vended environment: a resource group that already says who owns it and who pays for it,
# a budget that warns the owner before the money runs out, access for the team's Entra ID groups,
# and optionally a network with a security group on its subnet.

locals {
  name = "${var.team}-${var.environment}"

  # Every taggable resource gets the same tags Terraform sets. The baseline policy would add the
  # first three anyway; setting them here means Terraform never sees the policy's change as drift.
  tags = {
    owner         = var.owner
    "cost-center" = var.cost_center
    environment   = var.environment
    team          = var.team
    "vended-by"   = "guardrails"
    request       = var.request_file
  }

  budget_alerts = [
    { threshold = 50, type = "Actual" },
    { threshold = 80, type = "Actual" },
    { threshold = 100, type = "Actual" },
    { threshold = 100, type = "Forecasted" },
  ]
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_consumption_budget_resource_group" "this" {
  name              = "budget-${local.name}"
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.monthly_budget
  time_grain        = "Monthly"

  time_period {
    # Budgets start on the first of a month; this is the month the environment was created.
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", plantimestamp())
  }

  dynamic "notification" {
    for_each = local.budget_alerts
    content {
      enabled        = true
      threshold      = notification.value.threshold
      threshold_type = notification.value.type
      operator       = "GreaterThanOrEqualTo"
      contact_emails = [var.owner]
    }
  }

  lifecycle {
    ignore_changes = [time_period] # the start date is fixed once created
  }
}

resource "azurerm_role_assignment" "contributor" {
  for_each = var.contributor_groups

  scope                = azurerm_resource_group.this.id
  role_definition_name = "Contributor"
  principal_id         = each.value
  principal_type       = "Group"
  description          = "${each.key}: Contributor on ${local.name}, from ${var.request_file}"
}

resource "azurerm_role_assignment" "reader" {
  for_each = var.reader_groups

  scope                = azurerm_resource_group.this.id
  role_definition_name = "Reader"
  principal_id         = each.value
  principal_type       = "Group"
  description          = "${each.key}: Reader on ${local.name}, from ${var.request_file}"
}

resource "azurerm_management_lock" "prod" {
  count = var.environment == "prod" ? 1 : 0

  name       = "prod-no-delete"
  scope      = azurerm_resource_group.this.id
  lock_level = "CanNotDelete"
  notes      = "Prod environment from ${var.request_file}. Retiring it means removing the request in a reviewed pull request."
}

# --- Network (optional) ------------------------------------------------------------------------------

resource "azurerm_virtual_network" "this" {
  count = var.address_space == null ? 0 : 1

  name                = "vnet-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [var.address_space]
  tags                = local.tags
}

resource "azurerm_network_security_group" "workloads" {
  count = var.address_space == null ? 0 : 1

  name                = "nsg-${local.name}-workloads"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.tags
  # No rules of its own: Azure's defaults allow traffic inside the network and deny inbound
  # traffic from the internet. Teams add rules for what they run.
}

resource "azurerm_subnet" "workloads" {
  count = var.address_space == null ? 0 : 1

  name                 = "snet-workloads"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this[0].name
  # The first half of the address space; the second half is kept for private endpoints and
  # anything the team adds later.
  address_prefixes                = [cidrsubnet(var.address_space, 1, 0)]
  default_outbound_access_enabled = false # outbound internet only through something deliberate
}

resource "azurerm_subnet_network_security_group_association" "workloads" {
  count = var.address_space == null ? 0 : 1

  subnet_id                 = azurerm_subnet.workloads[0].id
  network_security_group_id = azurerm_network_security_group.workloads[0].id
}
