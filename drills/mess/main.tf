# The mess: a forgotten proof of concept, the kind Guardrails exists to find. Two VMs left running,
# a storage account anyone can read, a database ten times bigger than it needs, and things nobody
# deleted: a disk, a public IP and an App Service plan with no apps. Nothing but the resource group
# has a tag.
#
# A time-limited exemption lets it be created under the guardrails, as if it predated them.
# drills/drill.py then removes the exemption and times how long Azure Policy takes to find it all,
# and how long it takes to fix.

data "azurerm_client_config" "current" {}

locals {
  name = "legacy-poc"
}

# The group itself has to pass the baseline to be created at all.
resource "azurerm_resource_group" "mess" {
  name     = "rg-${local.name}"
  location = var.location
  tags = {
    owner         = var.owner
    "cost-center" = "cc-0000"
    environment   = "dev"
  }
}

resource "azurerm_resource_group_policy_exemption" "seed" {
  count = var.exempt ? 1 : 0

  name                 = "mess-drill-seed"
  resource_group_id    = azurerm_resource_group.mess.id
  policy_assignment_id = var.baseline_assignment_id
  exemption_category   = "Waiver"
  description          = "Mess drill: lets non-compliant resources be seeded, then is removed to start the clock."
  expires_on           = timeadd(plantimestamp(), "48h")
  metadata             = jsonencode({ owner = var.owner, ticket = "drills/README" })

  lifecycle {
    ignore_changes = [expires_on]
  }
}

# --- Two VMs left running ------------------------------------------------------------------------

resource "azurerm_virtual_network" "mess" {
  name                = "vnet-${local.name}"
  resource_group_name = azurerm_resource_group.mess.name
  location            = azurerm_resource_group.mess.location
  address_space       = ["10.199.0.0/24"]
  depends_on          = [azurerm_resource_group_policy_exemption.seed]

  lifecycle {
    ignore_changes = [tags] # the policies add tags; that's the point
  }
}

resource "azurerm_subnet" "mess" {
  #checkov:skip=CKV2_AZURE_31:Drill resources are deliberately non-compliant and live for an hour.
  name                            = "snet-default"
  resource_group_name             = azurerm_resource_group.mess.name
  virtual_network_name            = azurerm_virtual_network.mess.name
  address_prefixes                = ["10.199.0.0/25"]
  default_outbound_access_enabled = false
}

resource "azurerm_network_interface" "vm" {
  count = 2

  name                = "nic-poc-0${count.index + 1}"
  resource_group_name = azurerm_resource_group.mess.name
  location            = azurerm_resource_group.mess.location
  depends_on          = [azurerm_resource_group_policy_exemption.seed]

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.mess.id
    private_ip_address_allocation = "Dynamic"
  }

  lifecycle {
    ignore_changes = [tags]
  }
}

resource "azurerm_linux_virtual_machine" "vm" {
  #checkov:skip=CKV_AZURE_50:No extensions are installed; the drill VMs only need to exist.
  #checkov:skip=CKV_AZURE_179:No VM agent extensions are needed for the drill.
  #checkov:skip=CKV2_AZURE_10:Antimalware isn't needed on throwaway drill VMs.
  #checkov:skip=CKV2_AZURE_12:Backup isn't needed on throwaway drill VMs.
  count = 2

  name                            = "vm-poc-0${count.index + 1}"
  resource_group_name             = azurerm_resource_group.mess.name
  location                        = azurerm_resource_group.mess.location
  size                            = "Standard_B1s" # the cheapest VM that runs Linux comfortably
  admin_username                  = "drill"
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.vm[count.index].id]
  depends_on                      = [azurerm_resource_group_policy_exemption.seed]

  admin_ssh_key {
    username   = "drill"
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  lifecycle {
    ignore_changes = [tags]
  }
}

# --- Storage anyone can read ---------------------------------------------------------------------------

# tflint-ignore: azurerm_resources_missing_prevent_destroy # the drill is built to be destroyed
resource "azurerm_storage_account" "exports" {
  #checkov:skip=CKV_AZURE_190:Public blob access is the mess the drill finds and fixes.
  #checkov:skip=CKV_AZURE_59:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV_AZURE_33:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV_AZURE_206:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_1:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_33:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_38:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_40:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_41:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_47:Public blob access is the mess the drill finds and fixes.
  #checkov:skip=CKV_AZURE_43:The name is computed, which Checkov can't resolve.
  name                            = "stpoc${substr(sha1(data.azurerm_client_config.current.subscription_id), 0, 12)}"
  resource_group_name             = azurerm_resource_group.mess.name
  location                        = azurerm_resource_group.mess.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = !var.fixed # the mess: anonymous reads allowed
  depends_on                      = [azurerm_resource_group_policy_exemption.seed]

  lifecycle {
    ignore_changes = [tags]
  }
}

# --- A database ten times too big ------------------------------------------------------------------------

# tflint-ignore: azurerm_resources_missing_prevent_destroy # the drill is built to be destroyed
resource "azurerm_mssql_server" "poc" {
  #checkov:skip=CKV_AZURE_23:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV_AZURE_24:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_2:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_45:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV_AZURE_113:Drill resources are deliberately non-compliant and live for an hour.
  name                          = "sql-poc-${substr(sha1(data.azurerm_client_config.current.subscription_id), 0, 8)}"
  resource_group_name           = azurerm_resource_group.mess.name
  location                      = azurerm_resource_group.mess.location
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = false
  depends_on                    = [azurerm_resource_group_policy_exemption.seed]

  azuread_administrator {
    login_username              = var.owner
    object_id                   = data.azurerm_client_config.current.object_id
    azuread_authentication_only = true # no SQL passwords anywhere
  }

  lifecycle {
    ignore_changes = [tags]
  }
}

# tflint-ignore: azurerm_resources_missing_prevent_destroy # the drill is built to be destroyed
resource "azurerm_mssql_database" "poc" {
  #checkov:skip=CKV_AZURE_224:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV_AZURE_229:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_18:Drill resources are deliberately non-compliant and live for an hour.
  name        = "sqldb-poc"
  server_id   = azurerm_mssql_server.poc.id
  sku_name    = var.fixed ? "S0" : "S3" # 10 DTUs is plenty for an empty database; S3 is 100
  max_size_gb = 2

  lifecycle {
    ignore_changes = [tags]
  }
}

# --- Things nobody deleted --------------------------------------------------------------------------------

resource "azurerm_managed_disk" "orphan" {
  #checkov:skip=CKV_AZURE_93:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV_AZURE_251:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV2_AZURE_9:Drill resources are deliberately non-compliant and live for an hour.
  count = var.fixed ? 0 : 1

  name                 = "disk-poc-data-old"
  resource_group_name  = azurerm_resource_group.mess.name
  location             = azurerm_resource_group.mess.location
  storage_account_type = "Standard_LRS"
  create_option        = "Empty"
  disk_size_gb         = 32
  depends_on           = [azurerm_resource_group_policy_exemption.seed]

  lifecycle {
    ignore_changes = [tags]
  }
}

resource "azurerm_public_ip" "orphan" {
  count = var.fixed ? 0 : 1

  name                = "pip-poc-old"
  resource_group_name = azurerm_resource_group.mess.name
  location            = azurerm_resource_group.mess.location
  allocation_method   = "Static"
  sku                 = "Standard"
  depends_on          = [azurerm_resource_group_policy_exemption.seed]

  lifecycle {
    ignore_changes = [tags]
  }
}

resource "azurerm_service_plan" "empty" {
  #checkov:skip=CKV_AZURE_211:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV_AZURE_212:Drill resources are deliberately non-compliant and live for an hour.
  #checkov:skip=CKV_AZURE_225:Drill resources are deliberately non-compliant and live for an hour.
  count = var.fixed ? 0 : 1

  name                = "asp-poc-unused"
  resource_group_name = azurerm_resource_group.mess.name
  location            = azurerm_resource_group.mess.location
  os_type             = "Linux"
  sku_name            = "B1"
  depends_on          = [azurerm_resource_group_policy_exemption.seed]

  lifecycle {
    ignore_changes = [tags]
  }
}
