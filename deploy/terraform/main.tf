# InfoMesh Terraform Module
# Feature #98: Infrastructure as Code
#
# This module deploys an InfoMesh node on a cloud VM.
# Supports AWS, GCP, Azure via provider configuration.

terraform {
  required_version = ">= 1.5"
}

variable "instance_name" {
  description = "Name for the InfoMesh instance"
  type        = string
  default     = "infomesh-node"
}

variable "data_dir" {
  description = "Data directory path"
  type        = string
  default     = "/var/lib/infomesh"
}

variable "mcp_port" {
  description = "MCP HTTP port"
  type        = number
  default     = 8081
}

variable "p2p_port" {
  description = "P2P libp2p port"
  type        = number
  default     = 4001
}

variable "admin_port" {
  description = "Admin API port"
  type        = number
  default     = 8080
}

variable "profile" {
  description = "Resource profile (minimal, balanced, contributor, dedicated)"
  type        = string
  default     = "balanced"
}

# Output the service configuration
output "service_config" {
  description = "InfoMesh service configuration"
  value = {
    name      = var.instance_name
    data_dir  = var.data_dir
    mcp_port  = var.mcp_port
    p2p_port  = var.p2p_port
    admin_port = var.admin_port
    profile   = var.profile
  }
}

# Cloud-init user data for VM provisioning
output "cloud_init" {
  description = "Cloud-init script for VM provisioning"
  value = <<-EOT
    #!/bin/bash
    set -euo pipefail

    # Install Python 3.12+ and uv
    apt-get update
    apt-get install -y python3.12 python3.12-venv

    # Install uv
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Create user
    useradd -r -s /bin/false -d ${var.data_dir} infomesh
    mkdir -p ${var.data_dir}
    chown infomesh:infomesh ${var.data_dir}

    # Install InfoMesh
    uv pip install infomesh

    # Install systemd service
    cat > /etc/systemd/system/infomesh.service << 'EOF'
    [Unit]
    Description=InfoMesh P2P Search Engine
    After=network-online.target

    [Service]
    Type=simple
    User=infomesh
    ExecStart=/usr/local/bin/infomesh serve --mcp-http
    Restart=on-failure
    Environment=INFOMESH_DATA_DIR=${var.data_dir}
    Environment=INFOMESH_PROFILE=${var.profile}

    [Install]
    WantedBy=multi-user.target
    EOF

    systemctl daemon-reload
    systemctl enable infomesh
    systemctl start infomesh
  EOT
}
