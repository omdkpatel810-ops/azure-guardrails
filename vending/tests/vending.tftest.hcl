# Offline tests: both providers are mocked, so these need no Azure account.

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

mock_provider "azuread" {
  mock_data "azuread_group" {
    defaults = { object_id = "33333333-3333-3333-3333-333333333333" }
  }
}

run "every_request_becomes_an_environment" {
  command = apply

  variables {
    requests_dir = "tests/requests"
  }

  assert {
    condition     = output.environments == { alpha-dev = "rg-alpha-dev", alpha-prod = "rg-alpha-prod" }
    error_message = "One environment per request file."
  }

  assert {
    condition     = toset(keys(data.azuread_group.team)) == toset(["grp-alpha", "grp-finance"])
    error_message = "Each group named in any request is looked up once."
  }
}

run "the_real_requests_plan" {
  command = plan

  assert {
    condition     = length(module.environment) == length(fileset("../environments", "*.json"))
    error_message = "Every file in environments/ is planned."
  }
}
