# The whole configuration runs end to end against a mocked provider: the modules wire
# together, and the shared settings in guardrails.auto.tfvars are valid.

mock_provider "azurerm" {
  mock_data "azurerm_role_definition" {
    defaults = { name = "Tag Contributor" }
  }
}

# The provider checks ID formats even when mocked.
override_resource {
  target = module.management_groups.azurerm_management_group.root
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails" }
}

override_resource {
  target = module.management_groups.azurerm_management_group.top
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails-top" }
}

override_resource {
  target = module.management_groups.azurerm_management_group.landing_zone
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails-landing-zone" }
}

override_resource {
  target = module.policy.azurerm_policy_definition.this
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails/providers/Microsoft.Authorization/policyDefinitions/under-test" }
}

override_resource {
  target = module.policy.azurerm_management_group_policy_set_definition.this
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails/providers/Microsoft.Authorization/policySetDefinitions/under-test" }
}

override_resource {
  target = module.policy.azurerm_management_group_policy_assignment.this
  values = { id = "/providers/Microsoft.Management/managementGroups/guardrails/providers/Microsoft.Authorization/policyAssignments/under-test" }
}

run "applies_end_to_end" {
  command = apply

  variables {
    subscription_placement = { "11111111-2222-3333-4444-555555555555" = "online" }
  }

  assert {
    condition     = length(output.management_group_ids) == 7 && length(output.assignment_ids) == 6
    error_message = "Expected 7 management groups and 6 policy assignments."
  }

  assert {
    condition     = output.management_group_ids["root"] == "/providers/Microsoft.Management/managementGroups/guardrails"
    error_message = "The management group IDs should reach the policy module."
  }
}
