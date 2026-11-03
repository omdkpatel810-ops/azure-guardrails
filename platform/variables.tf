variable "prefix" {
  description = "Name prefix for every management group."
  type        = string
  default     = "guardrails"
}

variable "subscription_placement" {
  description = "Which management group each subscription belongs in: subscription ID => platform, corp, online, sandbox or decommissioned."
  type        = map(string)
  default     = {}
}

variable "allowed_locations" {
  description = "Regions resources may use."
  type        = list(string)
  default     = ["canadacentral", "canadaeast"]
}

variable "exemptions" {
  description = "Time-limited policy exemptions; see modules/policy/variables.tf."
  type = map(object({
    scope      = string
    assignment = string
    category   = optional(string, "Waiver")
    reason     = string
    owner      = string
    ticket     = optional(string)
    expires_on = string
    rules      = optional(list(string), [])
  }))
  default = {}
}
