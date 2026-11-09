variable "github_repository" {
  description = "The repository whose workflows may sign in, as owner/name."
  type        = string
  default     = "omdkpatel810-ops/azure-guardrails"

  validation {
    condition     = can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "Write the repository as owner/name."
  }
}

variable "root_management_group" {
  description = "Name of the Guardrails root management group, which bootstrap.sh creates first."
  type        = string
  default     = "guardrails"
}

variable "location" {
  description = "Region for the state storage account."
  type        = string
  default     = "canadacentral"
}

variable "tags" {
  description = "Tags for the state resource group; the baseline policy requires all three."
  type        = map(string)
  default = {
    owner         = "platform-team"
    "cost-center" = "platform"
    environment   = "prod"
  }

  validation {
    condition     = alltrue([for t in ["owner", "cost-center", "environment"] : length(lookup(var.tags, t, "")) > 0])
    error_message = "Set owner, cost-center and environment."
  }
}

variable "demo_teams" {
  description = "Pretend teams to create groups for (grp-guardrails-<team>). Environment requests name these."
  type        = list(string)
  default     = ["payments", "storefront", "data", "support", "marketing", "finance"]
}
