# Offline tests: the azurerm provider is mocked, so these need no Azure account.

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      subscription_id = "00000000-0000-0000-0000-00000000bbbb"
      object_id       = "00000000-0000-0000-0000-00000000cccc"
    }
  }

  mock_resource "azurerm_resource_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-legacy-poc" }
  }

  mock_resource "azurerm_subnet" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-legacy-poc/providers/Microsoft.Network/virtualNetworks/vnet-legacy-poc/subnets/snet-default" }
  }

  mock_resource "azurerm_network_interface" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-legacy-poc/providers/Microsoft.Network/networkInterfaces/nic" }
  }

  mock_resource "azurerm_mssql_server" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-legacy-poc/providers/Microsoft.Sql/servers/sql-poc" }
  }
}

variables {
  baseline_assignment_id = "/providers/Microsoft.Management/managementGroups/guardrails-landing-zones/providers/Microsoft.Authorization/policyAssignments/gr-baseline"
  owner                  = "drill@example.com"
  ssh_public_key         = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMrX0MLU0PvWIjRZgjJz6e5exCEu5tA3dcTnX+MMs/kq guardrails-drill-test" # a public key only; its private half was never saved
}

run "seeding_makes_the_mess" {
  command = apply

  assert {
    condition     = length(azurerm_resource_group_policy_exemption.seed) == 1 && azurerm_resource_group_policy_exemption.seed[0].policy_assignment_id == var.baseline_assignment_id
    error_message = "While seeding, an exemption suspends the baseline for this resource group only."
  }

  assert {
    condition     = jsondecode(azurerm_resource_group_policy_exemption.seed[0].metadata).owner == "drill@example.com" && timecmp(azurerm_resource_group_policy_exemption.seed[0].expires_on, timeadd(plantimestamp(), "49h")) < 0
    error_message = "The exemption follows Guardrails' own rules: an owner, and an expiry within days."
  }

  assert {
    condition     = azurerm_storage_account.exports.allow_nested_items_to_be_public && azurerm_mssql_database.poc.sku_name == "S3"
    error_message = "The mess: public blob access, and a database ten times too big."
  }

  assert {
    condition     = length(azurerm_managed_disk.orphan) == 1 && length(azurerm_public_ip.orphan) == 1 && length(azurerm_service_plan.empty) == 1
    error_message = "The mess: a disk, a public IP and a plan nobody uses."
  }

  assert {
    condition     = length(output.resource_ids) == 11 && alltrue([for vm in azurerm_linux_virtual_machine.vm : vm.tags == null || length(vm.tags) == 0])
    error_message = "Eleven resources to find, and none of them tagged."
  }

  assert {
    condition     = azurerm_resource_group.mess.tags["owner"] == "drill@example.com" && azurerm_resource_group.mess.tags["cost-center"] != "" && azurerm_resource_group.mess.tags["environment"] != ""
    error_message = "The resource group itself passes the baseline, or it couldn't be created."
  }

  assert {
    condition     = azurerm_mssql_server.poc.azuread_administrator[0].azuread_authentication_only && !azurerm_mssql_server.poc.public_network_access_enabled
    error_message = "Even the mess has no SQL passwords and no public database endpoint."
  }
}

run "fixing_leaves_only_what_belongs" {
  command = apply

  variables {
    exempt = false
    fixed  = true
  }

  assert {
    condition     = length(azurerm_resource_group_policy_exemption.seed) == 0
    error_message = "The exemption is gone: the guardrails apply again."
  }

  assert {
    condition     = !azurerm_storage_account.exports.allow_nested_items_to_be_public && azurerm_mssql_database.poc.sku_name == "S0"
    error_message = "Storage is closed and the database is the right size."
  }

  assert {
    condition     = length(azurerm_managed_disk.orphan) + length(azurerm_public_ip.orphan) + length(azurerm_service_plan.empty) == 0 && length(output.resource_ids) == 8
    error_message = "The idle resources are deleted."
  }
}

run "refuses_a_drill_without_an_owner" {
  command = plan

  variables {
    owner = "someone"
  }

  expect_failures = [var.owner]
}
