# Offline tests: the azurerm provider is mocked, so these need no Azure account.

mock_provider "azurerm" {
  mock_data "azurerm_role_definition" {
    defaults = { name = "Tag Contributor" }
  }
}

# The provider checks ID formats even when mocked, so give each resource a realistic ID.
override_resource {
  target = azurerm_policy_definition.this
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails/providers/Microsoft.Authorization/policyDefinitions/under-test" }
}

override_resource {
  target = azurerm_management_group_policy_set_definition.this
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails/providers/Microsoft.Authorization/policySetDefinitions/under-test" }
}

override_resource {
  target = azurerm_management_group_policy_assignment.this
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails/providers/Microsoft.Authorization/policyAssignments/under-test" }
}

variables {
  policy_dir = "../../policies"
  management_group_ids = {
    root           = "/providers/Microsoft.Management/managementGroups/guardrails"
    platform       = "/providers/Microsoft.Management/managementGroups/guardrails-platform"
    landing-zones  = "/providers/Microsoft.Management/managementGroups/guardrails-landing-zones"
    corp           = "/providers/Microsoft.Management/managementGroups/guardrails-corp"
    online         = "/providers/Microsoft.Management/managementGroups/guardrails-online"
    sandbox        = "/providers/Microsoft.Management/managementGroups/guardrails-sandbox"
    decommissioned = "/providers/Microsoft.Management/managementGroups/guardrails-decommissioned"
  }
}

run "stores_every_definition_and_initiative_at_the_root" {
  command = apply

  assert {
    condition     = length(azurerm_policy_definition.this) == 8 && length(azurerm_management_group_policy_set_definition.this) == 2
    error_message = "Expected 8 custom definitions and 2 initiatives."
  }

  assert {
    condition = alltrue(concat(
      [for d in values(azurerm_policy_definition.this) : d.management_group_id == var.management_group_ids["root"]],
      [for i in values(azurerm_management_group_policy_set_definition.this) : i.management_group_id == var.management_group_ids["root"]],
    ))
    error_message = "Definitions belong at the root group so every group below can assign them."
  }

  assert {
    condition     = length(azurerm_management_group_policy_set_definition.this["baseline"].policy_definition_reference) == 8
    error_message = "The baseline has 3 required tags, 3 inherited tags and 2 storage rules."
  }

  assert {
    condition     = jsondecode(azurerm_policy_definition.this["allowed-locations"].policy_rule).then.effect == "[parameters('effect')]"
    error_message = "Effects are parameters, so the same rule can deny in one group and audit in another."
  }
}

run "assigns_each_rule_at_the_right_level" {
  command = apply

  assert {
    condition = toset(keys(azurerm_management_group_policy_assignment.this)) == toset([
      "root/gr-locations", "platform/gr-baseline", "landing-zones/gr-baseline",
      "sandbox/gr-baseline-audit", "corp/gr-no-public-ip", "decommissioned/gr-decommissioned",
    ])
    error_message = "Unexpected set of assignments."
  }

  assert {
    condition     = azurerm_management_group_policy_assignment.this["corp/gr-no-public-ip"].management_group_id == var.management_group_ids["corp"]
    error_message = "No-public-IP applies to corp only, not online."
  }

  assert {
    condition     = jsondecode(azurerm_management_group_policy_assignment.this["landing-zones/gr-baseline"].parameters).denyEffect.value == "Deny"
    error_message = "Landing zones should block baseline breaks."
  }

  assert {
    condition     = jsondecode(azurerm_management_group_policy_assignment.this["sandbox/gr-baseline-audit"].parameters).denyEffect.value == "Audit"
    error_message = "The sandbox should only report baseline breaks."
  }

  assert {
    condition     = alltrue([for a in values(azurerm_management_group_policy_assignment.this) : a.enforce])
    error_message = "Every assignment is enforced; audit mode comes from the effect parameter, not from switching enforcement off."
  }

  assert {
    condition     = alltrue([for a in values(azurerm_management_group_policy_assignment.this) : length(a.name) <= 24])
    error_message = "Management group assignment names can be at most 24 characters."
  }
}

run "passes_the_allowed_regions_to_the_data_residency_rules" {
  command = apply

  variables {
    allowed_locations = ["eastus", "westus2"]
  }

  assert {
    condition     = jsondecode(azurerm_management_group_policy_assignment.this["root/gr-locations"].parameters).allowedLocations.value == ["eastus", "westus2"]
    error_message = "allowed_locations should reach the data residency assignment."
  }

  assert {
    condition     = !can(jsondecode(azurerm_management_group_policy_assignment.this["landing-zones/gr-baseline"].parameters).allowedLocations)
    error_message = "Policies without an allowedLocations parameter shouldn't be given one."
  }
}

run "gives_identities_only_to_assignments_that_fix_tags" {
  command = apply

  assert {
    condition = toset([for k, a in azurerm_management_group_policy_assignment.this : k if length(a.identity) > 0]) == toset([
      "platform/gr-baseline", "landing-zones/gr-baseline", "sandbox/gr-baseline-audit",
    ])
    error_message = "Only the baseline (which copies tags) needs a managed identity."
  }

  assert {
    condition     = length(azurerm_role_assignment.remediation) == 3
    error_message = "Each baseline identity gets one role assignment."
  }

  assert {
    condition     = alltrue([for r in values(azurerm_role_assignment.remediation) : r.role_definition_name == "Tag Contributor"])
    error_message = "Tag inheritance needs Tag Contributor, not Contributor."
  }

  assert {
    condition     = azurerm_role_assignment.remediation["sandbox/gr-baseline-audit/4a9ae827-6dc8-4573-8ac7-8239d42aa03f"].scope == var.management_group_ids["sandbox"]
    error_message = "Each identity's role is scoped to its own assignment's group."
  }

  assert {
    condition     = azurerm_management_group_policy_assignment.this["platform/gr-baseline"].location == "canadacentral"
    error_message = "Assignments with an identity need a location."
  }
}

run "refuses_a_policy_that_asks_for_a_role_not_on_the_list" {
  command = plan

  override_data {
    target = data.azurerm_role_definition.remediation
    values = { name = "Owner" }
  }

  expect_failures = [data.azurerm_role_definition.remediation]
}

run "creates_exemptions_at_group_and_resource_group_scope" {
  command = apply

  variables {
    exemptions = {
      clipfind-indexer-region = {
        scope      = "online"
        assignment = "root/gr-locations"
        reason     = "Video Indexer isn't offered in Canada; the indexing account runs in East US."
        owner      = "owner@example.com"
        ticket     = "https://example.com/issues/1"
        expires_on = timeadd(timestamp(), "720h")
        rules      = ["resources-in-allowed-regions"]
      }
      legacy-share = {
        scope      = "/subscriptions/11111111-2222-3333-4444-555555555555/resourceGroups/rg-legacy"
        assignment = "landing-zones/gr-baseline"
        category   = "Mitigated"
        reason     = "Old file share; access is limited to the office network until it moves in December."
        owner      = "owner@example.com"
        expires_on = timeadd(timestamp(), "1440h")
      }
    }
  }

  assert {
    condition     = azurerm_management_group_policy_exemption.this["clipfind-indexer-region"].policy_definition_reference_ids == tolist(["resources-in-allowed-regions"])
    error_message = "Exemptions can name single rules inside an initiative."
  }

  assert {
    condition     = jsondecode(azurerm_management_group_policy_exemption.this["clipfind-indexer-region"].metadata).owner == "owner@example.com"
    error_message = "The owner is recorded on the exemption."
  }

  assert {
    condition     = azurerm_resource_group_policy_exemption.this["legacy-share"].exemption_category == "Mitigated"
    error_message = "A resource group ID makes a resource group exemption."
  }

  assert {
    condition     = length(azurerm_management_group_policy_exemption.this) == 1 && length(azurerm_resource_group_policy_exemption.this) == 1
    error_message = "Each exemption goes to exactly one kind of scope."
  }
}

run "refuses_an_expired_exemption" {
  command = plan

  variables {
    exemptions = {
      old = {
        scope      = "online"
        assignment = "root/gr-locations"
        reason     = "This exemption should have been cleaned up already."
        owner      = "owner@example.com"
        expires_on = "2020-01-01T00:00:00Z"
      }
    }
  }

  expect_failures = [var.exemptions]
}

run "refuses_an_exemption_longer_than_90_days" {
  command = plan

  variables {
    exemptions = {
      forever = {
        scope      = "online"
        assignment = "root/gr-locations"
        reason     = "Trying to exempt this for a whole year at once."
        owner      = "owner@example.com"
        expires_on = timeadd(timestamp(), "2184h")
      }
    }
  }

  expect_failures = [var.exemptions]
}

run "refuses_an_exemption_without_an_owner" {
  command = plan

  variables {
    exemptions = {
      nobody = {
        scope      = "online"
        assignment = "root/gr-locations"
        reason     = "Nobody is accountable for this exemption at all."
        owner      = "the team"
        expires_on = timeadd(timestamp(), "24h")
      }
    }
  }

  expect_failures = [var.exemptions]
}

run "refuses_an_exemption_without_a_real_reason" {
  command = plan

  variables {
    exemptions = {
      lazy = {
        scope      = "online"
        assignment = "root/gr-locations"
        reason     = "needed"
        owner      = "owner@example.com"
        expires_on = timeadd(timestamp(), "24h")
      }
    }
  }

  expect_failures = [var.exemptions]
}

run "refuses_an_exemption_for_an_assignment_that_doesnt_exist" {
  command = plan

  variables {
    exemptions = {
      typo = {
        scope      = "online"
        assignment = "online/gr-baseline"
        reason     = "The baseline is assigned to landing-zones, not online."
        owner      = "owner@example.com"
        expires_on = timeadd(timestamp(), "24h")
      }
    }
  }

  expect_failures = [azurerm_management_group_policy_exemption.this]
}
