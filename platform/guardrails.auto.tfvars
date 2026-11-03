# Settings every environment shares. Subscription IDs aren't secret, but they stay out of a
# public repo: the pipeline passes subscription_placement from a GitHub variable.

allowed_locations = ["canadacentral", "canadaeast"]

# Exemptions expire within 90 days and need an owner and a reason. Example:
#
# exemptions = {
#   video-indexer-region = {
#     scope      = "online"
#     assignment = "root/gr-locations"
#     reason     = "Video Indexer isn't offered in Canada yet; ClipFind's indexing account runs in East US."
#     owner      = "you@example.com"
#     ticket     = "https://github.com/omdkpatel810-ops/azure-guardrails/issues/1"
#     expires_on = "2027-01-31T23:59:59Z"
#     rules      = ["resources-in-allowed-regions"]
#   }
# }
exemptions = {}
