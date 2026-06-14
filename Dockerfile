FROM python:3.10-slim

# Evitar prompts interactivos durante la instalación
ENV DEBIAN_FRONTEND=noninteractive

# Instalar dependencias del sistema (ffmpeg vital para Agente 5, docker.io para self-maintenance)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    make \
    docker.io \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Instalar OpenClaw globalmente
RUN npm install -g openclaw

# Configurar directorio de trabajo
WORKDIR /app

# Instalar dependencias de Python primero (optimización de caché de Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Comando por defecto para iniciar el orquestador
CMD ["make", "start"]
