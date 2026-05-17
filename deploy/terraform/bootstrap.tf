# InfoMesh Bootstrap Cluster — Multi-Region Azure Deployment
#
# Deploys 3 geographically distributed bootstrap nodes:
#   - US East (primary)
#   - EU West
#   - Asia East
#
# Usage:
#   cd deploy/terraform
#   terraform init
#   terraform plan -var-file=bootstrap.tfvars
#   terraform apply -var-file=bootstrap.tfvars

terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}

variable "resource_group" {
  description = "Azure resource group name"
  type        = string
  default     = "infomesh-bootstrap"
}

variable "regions" {
  description = "Azure regions for bootstrap nodes"
  type = list(object({
    name     = string
    location = string
  }))
  default = [
    { name = "eastus", location = "East US" },
    { name = "westeurope", location = "West Europe" },
    { name = "eastasia", location = "East Asia" },
  ]
}

variable "vm_size" {
  description = "Azure VM size"
  type        = string
  default     = "Standard_B1s"
}

variable "admin_username" {
  description = "SSH admin username"
  type        = string
  default     = "infomesh"
}

variable "ssh_public_key_path" {
  description = "Path to SSH public key"
  type        = string
  default     = "~/.ssh/id_rsa.pub"
}

# ── Resource Group ──────────────────────────────────────────────────

resource "azurerm_resource_group" "bootstrap" {
  name     = var.resource_group
  location = var.regions[0].location
}

# ── Network Security Group (shared) ────────────────────────────────

resource "azurerm_network_security_group" "bootstrap" {
  name                = "infomesh-bootstrap-nsg"
  location            = azurerm_resource_group.bootstrap.location
  resource_group_name = azurerm_resource_group.bootstrap.name

  security_rule {
    name                       = "AllowP2P"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "4001"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowSSH"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowAdminAPI"
    priority                   = 300
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "8080"
    source_address_prefix      = "10.0.0.0/8"
    destination_address_prefix = "*"
  }
}

# ── Per-Region Resources ───────────────────────────────────────────

resource "azurerm_virtual_network" "bootstrap" {
  count               = length(var.regions)
  name                = "infomesh-vnet-${var.regions[count.index].name}"
  address_space       = ["10.${count.index}.0.0/16"]
  location            = var.regions[count.index].location
  resource_group_name = azurerm_resource_group.bootstrap.name
}

resource "azurerm_subnet" "bootstrap" {
  count                = length(var.regions)
  name                 = "infomesh-subnet-${var.regions[count.index].name}"
  resource_group_name  = azurerm_resource_group.bootstrap.name
  virtual_network_name = azurerm_virtual_network.bootstrap[count.index].name
  address_prefixes     = ["10.${count.index}.1.0/24"]
}

resource "azurerm_public_ip" "bootstrap" {
  count               = length(var.regions)
  name                = "infomesh-ip-${var.regions[count.index].name}"
  location            = var.regions[count.index].location
  resource_group_name = azurerm_resource_group.bootstrap.name
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_network_interface" "bootstrap" {
  count               = length(var.regions)
  name                = "infomesh-nic-${var.regions[count.index].name}"
  location            = var.regions[count.index].location
  resource_group_name = azurerm_resource_group.bootstrap.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.bootstrap[count.index].id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.bootstrap[count.index].id
  }
}

resource "azurerm_network_interface_security_group_association" "bootstrap" {
  count                     = length(var.regions)
  network_interface_id      = azurerm_network_interface.bootstrap[count.index].id
  network_security_group_id = azurerm_network_security_group.bootstrap.id
}

resource "azurerm_linux_virtual_machine" "bootstrap" {
  count               = length(var.regions)
  name                = "infomesh-bootstrap-${var.regions[count.index].name}"
  location            = var.regions[count.index].location
  resource_group_name = azurerm_resource_group.bootstrap.name
  size                = var.vm_size
  admin_username      = var.admin_username

  network_interface_ids = [
    azurerm_network_interface.bootstrap[count.index].id,
  ]

  admin_ssh_key {
    username   = var.admin_username
    public_key = file(var.ssh_public_key_path)
  }

  os_disk {
    caching              = "ReadOnly"
    storage_account_type = "Standard_LRS"
    disk_size_gb         = 30
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  custom_data = base64encode(<<-EOT
    #!/bin/bash
    set -euo pipefail

    # Install Python 3.12+ and dependencies
    apt-get update && apt-get install -y python3.12 python3.12-venv curl

    # Install uv
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"

    # Create infomesh user
    useradd -r -s /bin/false -d /var/lib/infomesh -m infomesh

    # Install InfoMesh with P2P support
    uv pip install --system 'infomesh[p2p]'

    # Configure as bootstrap node
    mkdir -p /var/lib/infomesh
    cat > /var/lib/infomesh/config.toml << 'CONF'
    [node]
    listen_port = 4001
    role = "full"

    [resources]
    profile = "dedicated"

    [crawl]
    urls_per_hour = 120
    CONF

    chown -R infomesh:infomesh /var/lib/infomesh

    # Install systemd service
    cat > /etc/systemd/system/infomesh.service << 'SVC'
    [Unit]
    Description=InfoMesh Bootstrap Node
    After=network-online.target
    Wants=network-online.target

    [Service]
    Type=simple
    User=infomesh
    ExecStart=/usr/local/bin/infomesh serve
    Restart=always
    RestartSec=10
    Environment=INFOMESH_NODE_DATA_DIR=/var/lib/infomesh
    WatchdogSec=300

    [Install]
    WantedBy=multi-user.target
    SVC

    systemctl daemon-reload
    systemctl enable infomesh
    systemctl start infomesh
  EOT
  )

  tags = {
    project = "infomesh"
    role    = "bootstrap"
    region  = var.regions[count.index].name
  }
}

# ── Outputs ────────────────────────────────────────────────────────

output "bootstrap_ips" {
  description = "Public IPs of bootstrap nodes"
  value = {
    for i, r in var.regions :
    r.name => azurerm_public_ip.bootstrap[i].ip_address
  }
}

output "nodes_json" {
  description = "Generated bootstrap/nodes.json entries"
  value = [
    for i, r in var.regions : {
      addr   = "/ip4/${azurerm_public_ip.bootstrap[i].ip_address}/tcp/4001"
      region = r.name
      note   = "Azure ${r.location} bootstrapper"
    }
  ]
}

output "ssh_commands" {
  description = "SSH commands to access each node"
  value = {
    for i, r in var.regions :
    r.name => "ssh ${var.admin_username}@${azurerm_public_ip.bootstrap[i].ip_address}"
  }
}
