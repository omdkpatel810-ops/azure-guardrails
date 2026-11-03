# Offline tests: the azurerm provider is mocked, so these need no Azure account.

mock_provider "azurerm" {}

# The provider checks ID formats even when mocked, so give each level a realistic, distinct ID.
override_resource {
  target = azurerm_management_group.root
  values = { id = "/providers/Microsoft.Management/managementGroups/root-under-test" }
}

override_resource {
  target = azurerm_management_group.top
  values = { id = "/providers/Microsoft.Management/managementGroups/top-under-test" }
}

override_resource {
  target = azurerm_management_group.landing_zone
  values = { id = "/providers/Microsoft.Management/managementGroups/landing-zone-under-test" }
}

run "builds_the_landing_zone_hierarchy" {
  command = apply

  assert {
    condition     = toset(keys(output.ids)) == toset(["root", "platform", "landing-zones", "corp", "online", "sandbox", "decommissioned"])
    error_message = "Expected seven groups: root, platform, landing-zones, corp, online, sandbox, decommissioned."
  }

  assert {
    condition     = output.names["root"] == "guardrails" && output.names["corp"] == "guardrails-corp"
    error_message = "Group names should be the prefix plus the key."
  }

  assert {
    condition     = alltrue([for g in values(azurerm_management_group.top) : g.parent_management_group_id == azurerm_management_group.root.id])
    error_message = "Platform, landing zones, sandbox and decommissioned should sit directly under the root group."
  }

  assert {
    condition     = alltrue([for g in values(azurerm_management_group.landing_zone) : g.parent_management_group_id == azurerm_management_group.top["landing-zones"].id])
    error_message = "Corp and online should sit under landing zones."
  }

  assert {
    condition     = length(azurerm_management_group_subscription_association.this) == 0
    error_message = "No subscriptions are placed unless asked."
  }
}

run "places_subscriptions_in_the_named_group" {
  command = apply

  variables {
    prefix = "contoso"
    subscription_placement = {
      "11111111-2222-3333-4444-555555555555" = "online"
      "66666666-7777-8888-9999-000000000000" = "sandbox"
    }
  }

  assert {
    condition     = azurerm_management_group_subscription_association.this["11111111-2222-3333-4444-555555555555"].management_group_id == azurerm_management_group.landing_zone["online"].id
    error_message = "The first subscription should be in the online group."
  }

  assert {
    condition     = azurerm_management_group_subscription_association.this["66666666-7777-8888-9999-000000000000"].management_group_id == azurerm_management_group.top["sandbox"].id
    error_message = "The second subscription should be in the sandbox group."
  }

  assert {
    condition     = azurerm_management_group_subscription_association.this["66666666-7777-8888-9999-000000000000"].subscription_id == "/subscriptions/66666666-7777-8888-9999-000000000000"
    error_message = "Subscriptions are associated by their resource ID."
  }

  assert {
    condition     = output.names["online"] == "contoso-online"
    error_message = "The prefix should name every group."
  }
}

run "refuses_subscriptions_in_the_root_or_landing_zones_group" {
  command = plan

  variables {
    subscription_placement = { "11111111-2222-3333-4444-555555555555" = "landing-zones" }
  }

  expect_failures = [var.subscription_placement]
}

run "refuses_a_subscription_id_that_isnt_a_guid" {
  command = plan

  variables {
    subscription_placement = { "my-subscription" = "online" }
  }

  expect_failures = [var.subscription_placement]
}

run "refuses_a_bad_prefix" {
  command = plan

  variables {
    prefix = "Guard Rails"
  }

  expect_failures = [var.prefix]
}
