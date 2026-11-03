variable "policy_dir" {
  description = "Folder holding definitions/, initiatives/ and assignments.json."
  type        = string
}

variable "management_group_ids" {
  description = "Resource ID of each management group, by key (root, platform, landing-zones, corp, online, sandbox, decommissioned)."
  type        = map(string)

  validation {
    condition     = contains(keys(var.management_group_ids), "root")
    error_message = "The map needs a \"root\" group: definitions are stored there so every group below can use them."
  }
}

variable "allowed_locations" {
  description = "Regions resources may use. Passed to every assignment whose policy takes allowedLocations."
  type        = list(string)
  default     = ["canadacentral", "canadaeast"]

  validation {
    condition     = length(var.allowed_locations) > 0 && alltrue([for l in var.allowed_locations : can(regex("^[a-z0-9]+$", l))])
    error_message = "List at least one region, by its short name such as canadacentral."
  }
}

variable "identity_location" {
  description = "Region for the managed identities that tag-inheritance assignments use to fix resources."
  type        = string
  default     = "canadacentral"
}

variable "allowed_remediation_roles" {
  description = "Roles a policy may ask for to fix resources. Planning fails if a policy asks for any other role, so a policy change can't quietly hand out Owner."
  type        = list(string)
  default     = ["Tag Contributor"]
}

variable "exemptions" {
  description = <<-EOT
    Time-limited exceptions, by name. Each one says who owns it and why, and expires within 90 days.
      scope       management group key (such as "online"), or a resource group ID
      assignment  "<group key>/<assignment name>", such as "landing-zones/gr-baseline"
      rules       reference IDs inside an initiative to exempt; empty means all of them
  EOT
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

  validation {
    condition     = alltrue([for name in keys(var.exemptions) : can(regex("^[a-z0-9][a-z0-9-]{2,62}$", name))])
    error_message = "Name exemptions with 3 to 63 lowercase letters, digits and hyphens."
  }

  validation {
    condition     = alltrue([for e in values(var.exemptions) : contains(["Waiver", "Mitigated"], e.category)])
    error_message = "An exemption's category is Waiver (accepting the risk for now) or Mitigated (handled another way)."
  }

  validation {
    condition     = alltrue([for e in values(var.exemptions) : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", e.owner))])
    error_message = "Every exemption needs an owner's email address."
  }

  validation {
    condition     = alltrue([for e in values(var.exemptions) : length(trimspace(e.reason)) >= 20])
    error_message = "Every exemption needs a reason of at least 20 characters."
  }

  validation {
    condition     = alltrue([for e in values(var.exemptions) : can(timecmp(e.expires_on, "2000-01-01T00:00:00Z"))])
    error_message = "Write expires_on as an RFC 3339 time, such as 2027-01-31T23:59:59Z."
  }

  validation {
    condition = alltrue([
      for e in values(var.exemptions) :
      can(timecmp(e.expires_on, "2000-01-01T00:00:00Z")) ? (
        timecmp(e.expires_on, plantimestamp()) > 0 &&
        timecmp(e.expires_on, timeadd(plantimestamp(), "2160h")) <= 0
      ) : true
    ])
    error_message = "An exemption must expire in the future and within 90 days. Renew it with a new pull request if it's still needed."
  }
}
