# Policy as code: definitions, initiatives and assignments all come from the JSON files in policy_dir,
# the same files the Python evaluator in tools/ tests.

locals {
  definitions = {
    for file in fileset("${var.policy_dir}/definitions", "*.json") :
    trimsuffix(file, ".json") => jsondecode(file("${var.policy_dir}/definitions/${file}"))
  }
  initiatives = {
    for file in fileset("${var.policy_dir}/initiatives", "*.json") :
    trimsuffix(file, ".json") => jsondecode(file("${var.policy_dir}/initiatives/${file}"))
  }
  assignments = {
    for a in jsondecode(file("${var.policy_dir}/assignments.json")) : "${a.scope}/${a.name}" => a
  }

  # The roles each policy asks for to fix resources (Modify and DeployIfNotExists), by role GUID.
  definition_roles = {
    for key, d in local.definitions : key => [
      for id in try(d.properties.policyRule.then.details.roleDefinitionIds, []) : regex("[0-9a-f-]{36}$", lower(id))
    ]
  }
  policy_roles = merge(
    { for key, roles in local.definition_roles : "definition:${key}" => roles },
    { for key, i in local.initiatives : "initiative:${key}" => distinct(flatten([for p in i.policies : local.definition_roles[p.definition]])) },
  )
  policy_parameters = merge(
    { for key, d in local.definitions : "definition:${key}" => keys(try(d.properties.parameters, {})) },
    { for key, i in local.initiatives : "initiative:${key}" => keys(try(i.parameters, {})) },
  )
  policy_ids = merge(
    { for key, d in azurerm_policy_definition.this : "definition:${key}" => d.id },
    { for key, i in azurerm_management_group_policy_set_definition.this : "initiative:${key}" => i.id },
  )

  remediation = {
    for pair in flatten([
      for key, a in local.assignments : [for role in local.policy_roles[a.policy] : { assignment = key, role = role }]
    ]) : "${pair.assignment}/${pair.role}" => pair
  }
}

resource "azurerm_policy_definition" "this" {
  for_each = local.definitions

  name                = each.value.name
  policy_type         = "Custom"
  mode                = each.value.properties.mode
  display_name        = each.value.properties.displayName
  description         = each.value.properties.description
  management_group_id = var.management_group_ids["root"]
  metadata            = jsonencode(each.value.properties.metadata)
  parameters          = jsonencode(each.value.properties.parameters)
  policy_rule         = jsonencode(each.value.properties.policyRule)
}

resource "azurerm_management_group_policy_set_definition" "this" {
  for_each = local.initiatives

  name                = each.value.name
  policy_type         = "Custom"
  display_name        = each.value.displayName
  description         = each.value.description
  management_group_id = var.management_group_ids["root"]
  metadata            = jsonencode(each.value.metadata)
  parameters          = jsonencode(each.value.parameters)

  dynamic "policy_definition_reference" {
    for_each = each.value.policies
    content {
      reference_id         = policy_definition_reference.value.referenceId
      policy_definition_id = azurerm_policy_definition.this[policy_definition_reference.value.definition].id
      parameter_values     = jsonencode(policy_definition_reference.value.parameters)
    }
  }
}

resource "azurerm_management_group_policy_assignment" "this" {
  for_each = local.assignments

  name                 = each.value.name
  display_name         = each.value.displayName
  description          = each.value.description
  management_group_id  = var.management_group_ids[each.value.scope]
  policy_definition_id = local.policy_ids[each.value.policy]
  enforce              = true
  location             = length(local.policy_roles[each.value.policy]) > 0 ? var.identity_location : null

  parameters = jsonencode({
    for name, value in merge(
      each.value.parameters,
      contains(local.policy_parameters[each.value.policy], "allowedLocations") ? { allowedLocations = var.allowed_locations } : {},
    ) : name => { value = value }
  })

  dynamic "identity" {
    for_each = length(local.policy_roles[each.value.policy]) > 0 ? [1] : []
    content {
      type = "SystemAssigned"
    }
  }

  dynamic "non_compliance_message" {
    for_each = try([each.value.nonComplianceMessage], [])
    content {
      content = non_compliance_message.value
    }
  }

  lifecycle {
    precondition {
      condition     = length(each.value.name) <= 24
      error_message = "Management group assignment names can be at most 24 characters: ${each.value.name}."
    }
  }
}

# Look up each role a policy asks for, and refuse any role that isn't on the allowed list.
data "azurerm_role_definition" "remediation" {
  for_each = toset([for r in values(local.remediation) : r.role])

  role_definition_id = each.value
  scope              = var.management_group_ids["root"]

  lifecycle {
    postcondition {
      condition     = contains(var.allowed_remediation_roles, self.name)
      error_message = "A policy asks for the role ${self.name} (${each.value}) to fix resources, which isn't in allowed_remediation_roles."
    }
  }
}

resource "azurerm_role_assignment" "remediation" {
  for_each = local.remediation

  scope                = var.management_group_ids[local.assignments[each.value.assignment].scope]
  role_definition_name = data.azurerm_role_definition.remediation[each.value.role].name
  principal_id         = azurerm_management_group_policy_assignment.this[each.value.assignment].identity[0].principal_id
  principal_type       = "ServicePrincipal"
  description          = "Lets policy ${each.value.assignment} fix tags."
}

locals {
  resource_group_id = "^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+$"
}

resource "azurerm_management_group_policy_exemption" "this" {
  for_each = { for name, e in var.exemptions : name => e if !can(regex(local.resource_group_id, e.scope)) }

  name                            = each.key
  management_group_id             = try(var.management_group_ids[each.value.scope], null)
  policy_assignment_id            = try(azurerm_management_group_policy_assignment.this[each.value.assignment].id, null)
  exemption_category              = each.value.category
  expires_on                      = each.value.expires_on
  description                     = each.value.reason
  display_name                    = "${each.key} (owner ${each.value.owner})"
  policy_definition_reference_ids = each.value.rules
  metadata                        = jsonencode({ owner = each.value.owner, ticket = each.value.ticket })

  lifecycle {
    precondition {
      condition     = contains(keys(local.assignments), each.value.assignment)
      error_message = "Exemption ${each.key} names assignment ${each.value.assignment}, which doesn't exist."
    }
    precondition {
      condition     = contains(keys(var.management_group_ids), each.value.scope)
      error_message = "Exemption ${each.key} has scope ${each.value.scope}: use a management group key (${join(", ", keys(var.management_group_ids))}) or a resource group ID."
    }
  }
}

resource "azurerm_resource_group_policy_exemption" "this" {
  for_each = { for name, e in var.exemptions : name => e if can(regex(local.resource_group_id, e.scope)) }

  name                            = each.key
  resource_group_id               = each.value.scope
  policy_assignment_id            = try(azurerm_management_group_policy_assignment.this[each.value.assignment].id, null)
  exemption_category              = each.value.category
  expires_on                      = each.value.expires_on
  description                     = each.value.reason
  display_name                    = "${each.key} (owner ${each.value.owner})"
  policy_definition_reference_ids = each.value.rules
  metadata                        = jsonencode({ owner = each.value.owner, ticket = each.value.ticket })

  lifecycle {
    precondition {
      condition     = contains(keys(local.assignments), each.value.assignment)
      error_message = "Exemption ${each.key} names assignment ${each.value.assignment}, which doesn't exist."
    }
  }
}
