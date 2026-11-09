# Offline tests: the azurerm provider is mocked, so these need no Azure account.

mock_provider "azurerm" {
  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-under-test" }
  }

  mock_resource "azurerm_virtual_network" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-under-test/providers/Microsoft.Network/virtualNetworks/vnet-under-test" }
  }

  mock_resource "azurerm_subnet" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-under-test/providers/Microsoft.Network/virtualNetworks/vnet-under-test/subnets/snet-workloads" }
  }

  mock_resource "azurerm_network_security_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-under-test/providers/Microsoft.Network/networkSecurityGroups/nsg-under-test" }
  }
}

variables {
  team           = "payments"
  environment    = "dev"
  owner          = "payments-lead@example.com"
  cost_center    = "cc-2040"
  monthly_budget = 150
  request_file   = "payments-dev.json"
  contributor_groups = {
    grp-guardrails-payments = "11111111-1111-1111-1111-111111111111"
  }
  reader_groups = {
    grp-guardrails-finance = "22222222-2222-2222-2222-222222222222"
  }
  address_space = "10.100.1.0/24"
}

run "a_dev_environment" {
  command = apply

  assert {
    condition     = azurerm_resource_group.this.name == "rg-payments-dev" && azurerm_resource_group.this.location == "canadacentral"
    error_message = "The resource group is named after the team and environment."
  }

  assert {
    condition = azurerm_resource_group.this.tags == tomap({
      owner         = "payments-lead@example.com"
      "cost-center" = "cc-2040"
      environment   = "dev"
      team          = "payments"
      "vended-by"   = "guardrails"
      request       = "payments-dev.json"
    })
    error_message = "The resource group carries the three required tags, plus where it came from."
  }

  assert {
    condition     = azurerm_role_assignment.contributor["grp-guardrails-payments"].role_definition_name == "Contributor" && azurerm_role_assignment.contributor["grp-guardrails-payments"].principal_type == "Group"
    error_message = "The team's group gets Contributor on its dev environment."
  }

  assert {
    condition     = azurerm_role_assignment.reader["grp-guardrails-finance"].role_definition_name == "Reader"
    error_message = "Reader groups get Reader."
  }

  assert {
    condition     = length(azurerm_management_lock.prod) == 0
    error_message = "Dev environments aren't locked."
  }
}

run "budget_alerts_reach_the_owner" {
  command = apply

  assert {
    condition     = azurerm_consumption_budget_resource_group.this.amount == 150 && azurerm_consumption_budget_resource_group.this.time_grain == "Monthly"
    error_message = "The budget is the requested monthly amount."
  }

  assert {
    condition = toset([for n in azurerm_consumption_budget_resource_group.this.notification : "${n.threshold}-${n.threshold_type}"]) == toset([
      "50-Actual", "80-Actual", "100-Actual", "100-Forecasted",
    ])
    error_message = "Alerts at 50%, 80% and 100% of actual spend, and when the forecast passes 100%."
  }

  assert {
    condition     = alltrue([for n in azurerm_consumption_budget_resource_group.this.notification : n.contact_emails == tolist(["payments-lead@example.com"])])
    error_message = "Every alert goes to the owner."
  }

  assert {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-01T00:00:00Z$", azurerm_consumption_budget_resource_group.this.time_period[0].start_date))
    error_message = "Budgets start on the first of a month."
  }
}

run "the_network_has_a_guarded_subnet" {
  command = apply

  assert {
    condition     = azurerm_virtual_network.this[0].address_space == toset(["10.100.1.0/24"])
    error_message = "The network uses the requested address space."
  }

  assert {
    condition     = azurerm_subnet.workloads[0].address_prefixes == tolist(["10.100.1.0/25"]) && !azurerm_subnet.workloads[0].default_outbound_access_enabled
    error_message = "Workloads get the first half of the space, with no default outbound internet."
  }

  assert {
    condition     = azurerm_subnet_network_security_group_association.workloads[0].network_security_group_id == azurerm_network_security_group.workloads[0].id
    error_message = "The workloads subnet has a network security group."
  }

  assert {
    condition     = azurerm_virtual_network.this[0].tags == azurerm_resource_group.this.tags && azurerm_network_security_group.workloads[0].tags == azurerm_resource_group.this.tags
    error_message = "Network resources carry the same tags, so the inherit policy never causes drift."
  }

  assert {
    condition     = output.subnet_id != null
    error_message = "The subnet ID is an output for whatever the team deploys next."
  }
}

run "no_network_unless_asked" {
  command = apply

  variables {
    address_space = null
  }

  assert {
    condition     = length(azurerm_virtual_network.this) == 0 && length(azurerm_subnet.workloads) == 0 && output.subnet_id == null
    error_message = "No address space means no network."
  }
}

run "a_prod_environment_is_read_only_and_locked" {
  command = apply

  variables {
    environment        = "prod"
    request_file       = "payments-prod.json"
    contributor_groups = {}
    reader_groups = {
      grp-guardrails-payments = "11111111-1111-1111-1111-111111111111"
    }
  }

  assert {
    condition     = length(azurerm_role_assignment.contributor) == 0 && length(azurerm_role_assignment.reader) == 1
    error_message = "In prod the team reads; pipelines change things."
  }

  assert {
    condition     = azurerm_management_lock.prod[0].lock_level == "CanNotDelete"
    error_message = "Prod resource groups are locked against deletion."
  }
}

run "refuses_contributors_in_prod" {
  command = plan

  variables {
    environment  = "prod"
    request_file = "payments-prod.json"
  }

  expect_failures = [var.contributor_groups]
}

run "refuses_a_mismatched_request_file" {
  command = plan

  variables {
    request_file = "payments-prod.json"
  }

  expect_failures = [var.request_file]
}

run "refuses_a_zero_budget" {
  command = plan

  variables {
    monthly_budget = 0
  }

  expect_failures = [var.monthly_budget]
}
