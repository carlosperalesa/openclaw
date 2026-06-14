from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import subprocess
import os
import uuid
import logging

app = FastAPI(title="OpenClaw Docker Proxy")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DockerProxy")

class RunRequest(BaseModel):
    task_id: str
    file_path: str
    request: str
    mode: str
    dependencies: list[str] = []

@app.post("/run-skill")
async def run_skill(req: RunRequest, background_tasks: BackgroundTasks):
    """
    Recibe la orden de correr un skill en un contenedor efímero.
    Si requiere dependencias (PIP), crea una imagen hija dinámica.
    Luego ejecuta la skill y retorna la salida.
    """
    logger.info(f"Petición de ejecución recibida: {req.task_id} Modo: {req.mode}")
    
    # Validaciones básicas
    if ".." in req.file_path or req.file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Ruta de archivo inválida.")

    base_image = "python:3.10-slim"
    image_to_run = base_image
    
    # 1. Manejo de dependencias y construcción de imagen efímera
    if req.dependencies and req.mode == "SAFE":
        image_name = f"openclaw_worker_{req.task_id.lower()}"
        logger.info(f"Construyendo imagen efímera {image_name} con deps: {req.dependencies}")
        
        dockerfile_content = f"FROM {base_image}\nWORKDIR /app\nRUN pip install --no-cache-dir {' '.join(req.dependencies)}\n"
        df_path = f"/tmp/Dockerfile_{req.task_id}"
        
        with open(df_path, "w") as f:
            f.write(dockerfile_content)
            
        try:
            # Construir imagen sin contexto usando -f -
            subprocess.run(["docker", "build", "-t", image_name, "-f", df_path, "/tmp"], check=True)
            image_to_run = image_name
            
            # Limpiar el dockerfile temporal
            os.remove(df_path)
            
            # Programar eliminación de la imagen efímera después de usarla (opcional, o se puede limpiar periódicamente)
            def clean_image(img):
                subprocess.run(["docker", "rmi", img], check=False)
                
            background_tasks.add_task(clean_image, image_name)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Fallo al construir dependencias: {e}")
            raise HTTPException(status_code=500, detail=f"Error instalando PIP: {e}")

    # 2. Ejecución del contenedor
    # Los comandos asumen que el host tiene montado /data y /modules
    
    docker_cmd = [
        "docker", "run", "--rm",
        "--network", "bridge", # Aislado por defecto, no host
    ]
    
    # Montajes (Asumimos que el proxy corre en el mismo host que el core y comparte volúmenes)
    # NOTA: En un docker-compose real, el proxy necesitaría saber la ruta host absoluta, o usar volúmenes nombrados.
    # Usaremos volúmenes montados desde el proxy.
    
    # IMPORTANTE: Como el proxy lanza dockers hermanos, necesita las rutas absolutas del HOST.
    # Las pasaremos por variables de entorno al proxy.
    host_data_dir = os.environ.get("HOST_DATA_DIR", "/app/data")
    host_modules_dir = os.environ.get("HOST_MODULES_DIR", "/app/modules")
    host_env_file = os.environ.get("HOST_ENV_FILE", "/app/.env")
    
    docker_cmd.extend([
        "-v", f"{host_data_dir}:/app/data",
        "-v", f"{host_modules_dir}:/app/modules",
        "-v", f"{host_env_file}:/app/.env"
    ])
    
    docker_cmd.extend([image_to_run, "python"])
    
    if req.mode == "SAFE":
        docker_cmd.extend(["/app/" + req.file_path, req.request])
    else:
        # GOD mode (ya no recibe docker.sock, solo tiene acceso a modules y data completos)
        # Nota: Si el GOD mode intentara usar Docker nativo, fallaría aquí. Esta es la restricción de seguridad.
        docker_cmd.extend(["/app/modules/skill_runner.py", req.task_id, "/app/" + req.file_path, req.request])

    try:
        logger.info(f"Ejecutando contenedor: {' '.join(docker_cmd)}")
        result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=120)
        
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="El skill excedió el tiempo máximo de ejecución.")
    except Exception as e:
        logger.error(f"Error ejecutando docker run: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class ControlRequest(BaseModel):
    action: str
    container_name: str

@app.post("/docker-control")
async def docker_control(req: ControlRequest):
    """
    Control básico de contenedores (Pixy DevOps).
    Acciones permitidas: stop, restart, rm
    """
    valid_actions = ["stop", "restart", "rm"]
    if req.action not in valid_actions:
        raise HTTPException(status_code=400, detail="Acción no permitida.")
        
    try:
        cmd = ["docker", req.action, req.container_name]
        # Force rm si es rm
        if req.action == "rm":
            cmd = ["docker", "rm", "-f", req.container_name]
            
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return {"status": "ok", "message": result.stdout.strip()}
        else:
            raise HTTPException(status_code=500, detail=result.stderr.strip())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/spawn-agent")
async def spawn_agent(req: RunRequest):
    """
    Levanta una skill en modo AGENT (daemon background continuo).
    """
    logger.info(f"Petición de spawn-agent recibida: {req.task_id}")
    
    if ".." in req.file_path or req.file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Ruta de archivo inválida.")

    base_image = "python:3.10-slim"
    image_to_run = base_image
    
    if req.dependencies:
        image_name = f"openclaw_agent_img_{req.task_id.lower()}"
        dockerfile_content = f"FROM {base_image}\nWORKDIR /app\nRUN pip install --no-cache-dir {' '.join(req.dependencies)}\n"
        df_path = f"/tmp/Dockerfile_{req.task_id}"
        with open(df_path, "w") as f:
            f.write(dockerfile_content)
        try:
            subprocess.run(["docker", "build", "-t", image_name, "-f", df_path, "/tmp"], check=True)
            image_to_run = image_name
            os.remove(df_path)
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"Error construyendo imagen agente: {e}")

    container_name = f"agent_{req.task_id}"
    host_data_dir = os.environ.get("HOST_DATA_DIR", "/app/data")
    host_modules_dir = os.environ.get("HOST_MODULES_DIR", "/app/modules")
    host_env_file = os.environ.get("HOST_ENV_FILE", "/app/.env")
    
    docker_cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "--network", "openclaw_net", # Conectado a la misma red para ver a core y proxy si requiere
        "--restart", "unless-stopped",
        "-v", f"{host_data_dir}:/app/data",
        "-v", f"{host_modules_dir}:/app/modules",
        "-v", f"{host_env_file}:/app/.env",
        image_to_run, "python", "/app/" + req.file_path, req.request
    ]
    
    try:
        logger.info(f"Levantando agente: {' '.join(docker_cmd)}")
        result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return {
                "stdout": f"RESULTADO_OK: Agente levantado exitosamente. ID/Nombre: {container_name}",
                "stderr": "",
                "returncode": 0
            }
        else:
            return {
                "stdout": "",
                "stderr": result.stderr.strip(),
                "returncode": result.returncode
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cleanup-agents")
async def cleanup_agents():
    """
    Busca contenedores 'agent_*' que hayan fallado (status=exited).
    Extrae sus logs, los elimina, y devuelve el reporte.
    """
    try:
        # Buscar contenedores muertos
        cmd = ["docker", "ps", "-a", "--filter", "name=agent_", "--filter", "status=exited", "--format", "{{.Names}}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        dead_agents = [n.strip() for n in result.stdout.split("\n") if n.strip()]
        
        report = []
        log_dir = "/app/data/agent_logs"
        os.makedirs(log_dir, exist_ok=True)
        
        for agent in dead_agents:
            # Check exit code
            inspect_cmd = ["docker", "inspect", "--format", "{{.State.ExitCode}}", agent]
            res_inspect = subprocess.run(inspect_cmd, capture_output=True, text=True)
            exit_code = res_inspect.stdout.strip()
            
            # 0=Clean, 137=SIGKILL (docker stop timeout/kill), 143=SIGTERM (docker stop)
            if exit_code in ["0", "137", "143"]:
                continue
                
            # Obtener logs
            log_cmd = ["docker", "logs", "--tail", "500", agent]
            log_result = subprocess.run(log_cmd, capture_output=True, text=True)
            logs = log_result.stderr + "\n" + log_result.stdout
            logs = logs[-5000:] # Ultimos 5000 caracteres
            
            # Guardar logs en local
            log_file = os.path.join(log_dir, f"{agent}_crash.log")
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(logs)
            
            # Eliminar contenedor
            subprocess.run(["docker", "rm", "-f", agent])
            
            report.append({
                "agent": agent,
                "log_file": log_file
            })
            
        return {"dead_agents": report}
    except Exception as e:
        logger.error(f"Error limpiando agentes: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class LogRequest(BaseModel):
    resource_id: str
    tail: int = 100

@app.post("/docker-logs")
async def docker_logs(req: LogRequest):
    """
    Obtiene los logs de un contenedor a través de su resource_id.
    """
    # Mapeo determinista (Lista Blanca)
    resource_map = {
        "core_logs": "openclaw_core",
        "proxy_logs": "openclaw_docker_proxy"
    }
    
    # Soporte dinámico para agentes: agent_logs_<id> -> agent_<id>
    if req.resource_id.startswith("agent_logs_"):
        container_name = req.resource_id.replace("agent_logs_", "agent_")
    else:
        container_name = resource_map.get(req.resource_id)
        
    if not container_name:
        raise HTTPException(status_code=400, detail=f"Resource ID '{req.resource_id}' no reconocido o no autorizado.")
        
    import re
    if not re.match(r"^[a-zA-Z0-9_.-]+$", container_name):
        raise HTTPException(status_code=400, detail="Nombre de contenedor inválido mapeado.")
        
    try:
        cmd = ["docker", "logs", "--tail", str(req.tail), container_name]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return {"logs": result.stderr + "\n" + result.stdout}
        else:
            raise HTTPException(status_code=500, detail=f"No se pudieron leer logs del recurso {req.resource_id}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/docker-ps")
async def docker_ps():
    """
    Lista los contenedores activos.
    """
    try:
        cmd = ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return {"ps": result.stdout.strip() or "No hay contenedores."}
        else:
            raise HTTPException(status_code=500, detail="Fallo listando contenedores")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
