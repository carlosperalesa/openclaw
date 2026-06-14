#!/bin/bash
# setup_server.sh
# Script para configurar el servidor de producción en DigitalOcean

set -e

echo "=== Actualizando paquetes del sistema ==="
apt-get update && apt-get upgrade -y

echo "=== Configurando Swap de 8GB ==="
if swapon --show | grep -q "/swapfile"; then
    echo "Swap ya está configurado."
else
    fallocate -l 8G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab
    echo "Swap de 8GB creado exitosamente."
    
    # Ajustar swappiness
    sysctl vm.swappiness=10
    echo 'vm.swappiness=10' | tee -a /etc/sysctl.conf
fi

echo "=== Verificando Docker ==="
if ! command -v docker &> /dev/null; then
    echo "Instalando Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

if ! command -v docker-compose &> /dev/null; then
    echo "Instalando Docker Compose..."
    curl -L "https://github.com/docker/compose/releases/download/v2.24.5/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi

echo "=== Setup Completado ==="
