variable "prefix" {
  description = "Name prefix for every management group, such as \"guardrails\"."
  type        = string
  default     = "guardrails"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}[a-z0-9]$", var.prefix))
    error_message = "Use 3 to 32 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "parent_management_group_id" {
  description = "Resource ID of the group the hierarchy sits under. Null means the tenant root group."
  type        = string
  default     = null
}

variable "subscription_placement" {
  description = "Which management group each subscription belongs in: subscription ID => group key (platform, corp, online, sandbox or decommissioned)."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for key in values(var.subscription_placement) : contains(["platform", "corp", "online", "sandbox", "decommissioned"], key)])
    error_message = "Place subscriptions in platform, corp, online, sandbox or decommissioned. The root and landing-zones groups only hold other groups."
  }

  validation {
    condition     = alltrue([for id in keys(var.subscription_placement) : can(regex("^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$", id))])
    error_message = "Subscription IDs are GUIDs, such as 00000000-0000-0000-0000-000000000000."
  }
}
