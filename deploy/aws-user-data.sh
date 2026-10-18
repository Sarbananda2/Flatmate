#!/usr/bin/env bash
# AWS EC2 / Lightsail User Data (Cloud-Init) script
# Automatically prepares Ubuntu 24.04 LTS with Docker & Docker Compose
set -euo pipefail

echo "==> Updating system packages..."
apt-get update -y
apt-get upgrade -y

echo "==> Installing Docker..."
curl -fsSL https://get.docker.com | sh
usermod -aG docker ubuntu

echo "==> Ensuring Docker Compose is available..."
apt-get install -y docker-compose-plugin

echo "==> Docker installed successfully!"
docker --version
docker compose version

