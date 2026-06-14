import os
import logging
import hashlib
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.staticfiles import StaticFiles
import uvicorn

from modules import ModuleManager

import sys

os.makedirs("data/logs", exist_ok=True)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# File handler para CoreEyes
file_handler = logging.FileHandler("data/logs/openclaw.log", encoding='utf-8')
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

# Console handler para Docker logs
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

logger = logging.getLogger("Orchestrator")

app = FastAPI(title="OpenClaw Orchestrator")
manager = ModuleManager(config_dir="config", data_dir="data")

# Crear y servir carpeta pública para archivos generados por Khai
os.makedirs("data/public", exist_ok=True)
app.mount("/public", StaticFiles(directory="data/public"), name="public")

def format_health(result: dict) -> str:
    status_emoji = "✅" if result.get("status") == "ok" else "❌"
    text = f"{status_emoji} <b>System Health Report</b>\n\n"
    text += f"Time: {result.get('timestamp')}\n"
    text += f"File Structure: {result.get('file_structure', {}).get('status')}\n"
    text += f"Modules Health: {result.get('modules_health', {}).get('status')}\n"
    text += f"Config Validity: {result.get('config_validity', {}).get('status')}\n"
    return text

def restart_container_via_proxy(container_name: str) -> None:
    """Restart a container through the Docker proxy."""
    import requests

    proxy_url = os.environ.get("PROXY_URL", "http://docker_proxy:8001/run-skill").replace("/run-skill", "/docker-control")
    payload = {"action": "restart", "container_name": container_name}
    requests.post(proxy_url, json=payload, timeout=30)

def remember_eyes_observation(resource_id: str, output: str) -> None:
    """Guarda evidencia EYES con hash determinista para FastBeat."""
    manager.memory.append_long_term(
        "system_msg",
        f"[SYSTEM_EVENT] Evidencia EYES ('{resource_id}'):\n{output}",
        metadata={
            "source": "CoreEyes",
            "resource_id": resource_id,
            "output_hash": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        },
    )

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    logging.error(f"WEBHOOK RAW: {data}")
    user_response = None

    # BUG #6 FIX: Extraer user_response tanto de callback_query como de message
    if "callback_query" in data:
        user_response = data["callback_query"]["data"]
        try:
            import requests
            # BUG #2 FIX: manager.telegram.bot_token (no manager.publisher.telegram.bot_token)
            requests.post(f"https://api.telegram.org/bot{manager.telegram.bot_token}/answerCallbackQuery", 
                          json={"callback_query_id": data["callback_query"]["id"]}, timeout=5)
        except:
            pass
    elif "message" in data and "text" in data["message"]:
        incoming_id = str(data["message"]["from"]["id"])
        expected_id = str(manager.telegram.user_id)
        logging.error(f"COMPARANDO IDs -> Incoming: {incoming_id} | Expected: {expected_id}")
        if incoming_id == expected_id:
            user_response = data["message"]["text"]
        else:
            logging.error("¡ID NO COINCIDE! Ignorando mensaje.")
            
    logging.error(f"USER RESPONSE EXTRAIDO: {user_response}")

    # BUG #6 FIX: Procesamiento de user_response DESANIDADO — se ejecuta para callbacks Y mensajes
    if user_response:
        try:
            manager.telegram.send_chat_action("typing")
        except:
            pass
            
        # Intervención Meta-Agente: Pixy
        if user_response.lower().startswith("pixy"):
            background_tasks.add_task(manager.pixy.process_request, user_response)
            return {"ok": True}
            
        # Reglas Híbridas (Ahorro de Tokens)
        user_msg_lower = user_response.lower().strip()
        # Comandos Directos Especiales
        if user_msg_lower.startswith("/make ") or user_msg_lower.startswith("/do "):
            request_body = user_response.split(" ", 1)[1]
            if request_body.strip().lower() == "restart":
                # Alias a /restart
                user_msg_lower = "/restart"
            else:
                manager.telegram.send_message(text="⚙️ Comando directo recibido. Pasando directo a SkillForge...")
                import uuid
                task_id = f"task_{uuid.uuid4().hex[:8]}"
                background_tasks.add_task(manager.forge.resolve_request, task_id, request_body, manager.telegram, manager.state)
                return {"ok": True}
                
        if user_msg_lower in ["/restart", "restart"]:
            manager.telegram.send_message(text="🔄 <b>Reiniciando el Core de OpenClaw...</b>")
            background_tasks.add_task(restart_container_via_proxy, "openclaw_core")
            return {"ok": True}
        elif user_msg_lower in ["/help", "help", "ayuda"]:
            help_text = (
                "🤖 <b>OpenClaw Framework</b>\n\n"
                "<b>Comandos Directos (Sin costo de LLM):</b>\n"
                "• `/health` - Ejecuta un chequeo del sistema.\n"
                "• `/make [tarea]` - Manda la orden directa a SkillForge.\n"
                "• `/restart` - Reinicia el contenedor principal del bot.\n"
                "• `/new` - Borra la memoria conversacional.\n"
                "• `/compact` - Resume el chat actual para ahorrar tokens.\n"
                "• `pixy [orden]` - Habla directo con la supervisora DevOps.\n\n"
                "O simplemente háblame de forma natural y mi IA procesará tu intención."
            )
            manager.telegram.send_message(text=help_text)
            return {"ok": True}
        elif user_msg_lower == "/new":
            manager.memory.clear_chat_history()
            manager.telegram.send_message(text="🧹 <b>Memoria Conversacional Borrada.</b> Empezamos de cero.")
            return {"ok": True}
        elif user_msg_lower == "/compact":
            manager.telegram.send_message(text="🗜️ <b>Compactando Memoria...</b>")
            chat_history = manager.memory.get_chat_history(limit=500)
            if not chat_history:
                manager.telegram.send_message(text="No hay historial que compactar.")
                return {"ok": True}
            
            prompt = f"Actúa como un sintetizador experto. Resume la siguiente conversación detalladamente. NO omitas decisiones técnicas, configuración, comandos, URLs, ni contexto clave. Solo elimina el ruido conversacional:\n\n{chat_history}"
            
            try:
                summary = manager.llm.generate_content(prompt)
                manager.memory.clear_chat_history()
                manager.memory.append_long_term("system_msg", f"RESUMEN COMPACTADO:\n{summary}")
                manager.telegram.send_message(text="✅ Historial compactado y guardado como el nuevo punto de partida.")
            except Exception as e:
                manager.telegram.send_message(text=f"❌ Error compactando: {e}")
            return {"ok": True}
            
        eyes_loop = 0
        while eyes_loop < 3:
            try:
                intent_result = manager.core_mind.process_message(user_response)
                action = intent_result.get("action", "GENERAL_CHAT")
                reply = intent_result.get("reply", "")
                extracted_goal = intent_result.get("extracted_goal", "")
                monologue = intent_result.get("internal_monologue", "")
                investigate_target = intent_result.get("investigate_target", "")
                investigate_reason = intent_result.get("investigate_reason", "Unspecified")
                
                # Manejo de INVESTIGATE explícito
                if action == "INVESTIGATE" and investigate_target:
                    cmd = investigate_target.strip()
                    manager.telegram.send_message(text=f"👀 <i>Khai está investigando logs:</i> `{cmd}`\n<b>Razón:</b> <i>{investigate_reason}</i>")
                    eyes_res = manager.core_eyes.observe(cmd, investigate_reason)
                    remember_eyes_observation(cmd, eyes_res)
                    user_response = f"Sistema: He ejecutado tu comando EYES. Resultado guardado en memoria. Evalúa la evidencia y procede."
                    eyes_loop += 1
                    continue
                # Manejo Legacy de [EYES:] si el LLM lo inyecta en el reasoning
                elif monologue and "[EYES:" in monologue:
                    import re
                    match = re.search(r"\[EYES:\s*(.*?)\]", monologue)
                    if match:
                        cmd = match.group(1).strip()
                        manager.telegram.send_message(text=f"👀 <i>Khai está observando el sistema:</i> `{cmd}`")
                        eyes_res = manager.core_eyes.observe(cmd, "Legacy EYES call")
                        remember_eyes_observation(cmd, eyes_res)
                        user_response = f"Sistema: He ejecutado tu comando EYES. Resultado guardado en memoria. Evalúa la evidencia y procede."
                        eyes_loop += 1
                        continue
            except Exception as e:
                manager.core_heart.notify_error("CoreMind (Webhook Entry)", e)
                return {"ok": True}
            break

        if action == "FASTBEAT":
            report = manager.core_heart.run_diagnostic_fast()
            ram = report.get("ram", {})
            disk = report.get("disk", {})
            integrity = report.get("integrity", {})
            dead = report.get("dead_agents", [])
            errors = report.get("log_errors", [])
            ram_e = "🔴" if ram.get("status") == "critical" else "🟡" if ram.get("status") == "warning" else "🟢"
            disk_e = "🔴" if disk.get("status") == "critical" else "🟡" if disk.get("status") == "warning" else "🟢"
            lines = [
                "🫀 <b>CoreHeart — FastBeat</b>", "",
                f"{ram_e} <b>RAM:</b> {ram.get('used_pct', '?')}% ({ram.get('available_mb', '?')} MB libres)",
                f"{disk_e} <b>Disco:</b> {disk.get('used_pct', '?')}% ({disk.get('free_gb', '?')} GB libres)",
                f"📋 <b>Integridad:</b> {integrity.get('status', '?')}",
            ]
            if dead: lines.append(f"💀 <b>Agentes muertos:</b> {len(dead)}")
            if errors:
                lines.append(f"\n⚠️ <b>Errores recientes ({len(errors)}):</b>")
                for e in errors[:5]: lines.append(f"  <code>{e[:120]}</code>")
            if not errors and not dead and ram.get("status") == "ok":
                lines.append("\n✅ Sistema saludable.")
            manager.telegram.send_message(text="\n".join(lines))
            
        elif action == "SLOWBEAT":
            manager.telegram.send_message(text="🫀 <b>CoreHeart:</b> Ejecutando ciclo cognitivo (SlowBeat)...")
            background_tasks.add_task(lambda: manager.telegram.send_message(
                text=f"🫀 <b>CoreHeart — SlowBeat</b>\n\n<pre>{manager.core_heart.run_diagnostic_slow()}</pre>"
            ))
            
        elif action == "LOGS":
            logs = manager.core_eyes.observe(intent_result.get("target", "core_logs"), "User explicitly requested via command")
            manager.telegram.send_message(text=f"📋 <b>Logs (tail)</b>\n\n<pre>{logs[-3000:]}</pre>")
            
        elif action == "PS":
            containers = manager.core_eyes.observe("running_containers", "User explicitly requested via command")
            manager.telegram.send_message(text=f"🐳 <b>Contenedores</b>\n\n<pre>{containers}</pre>")
            
        elif action == "CLEANUP":
            import requests
            try:
                manager.telegram.send_message(text="🧹 Limpiando agentes muertos...")
                proxy_url = os.environ.get("PROXY_URL", "http://openclaw_docker_proxy:8000")
                resp = requests.get(f"{proxy_url}/cleanup-agents", timeout=30)
                manager.telegram.send_message(text=f"Resultado: {resp.json()}")
            except Exception as e:
                manager.telegram.send_message(text=f"Error: {e}")

        elif action == "HEALTH_CHECK":
            if reply: manager.telegram.send_message(text=reply)
            result = manager.core_heart.check_integrity()
            manager.telegram.send_message(text=format_health(result))
            
        elif action == "ACTION_REQUIRED":
            if reply: manager.telegram.send_message(text=reply)
            import uuid
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            
            # --- CAPA CAUSAL ---
            failure_analysis = intent_result.get("failure_analysis")
            hypothesis_id = None
            alternative_hypotheses = None
            if failure_analysis:
                evidence = failure_analysis.get("evidence", "")
                possible_causes = failure_analysis.get("possible_causes", [])
                if possible_causes:
                    hypothesis_id, alternative_hypotheses = manager.core_heart.register_multiple_hypotheses(task_id, possible_causes, evidence)
            
            target_request = extracted_goal if extracted_goal else user_response
            background_tasks.add_task(
                manager.forge.resolve_request,
                task_id,
                target_request,
                manager.telegram,
                manager.state,
                hypothesis_id=hypothesis_id,
                alternative_hypotheses=alternative_hypotheses,
            )
            
        elif action == "STATE_MACHINE_INPUT":
            # Verificar si hay habilidades pendientes de aprobación/negociación
            pending_tasks = manager.state.get_pending_tasks()
            handled = False
            for tid, tdata in pending_tasks.items():
                if tdata.get("status") == "WAITING_SKILL_APPROVAL":
                    handled = True
                    background_tasks.add_task(manager.forge.build_and_run_skill, tid, [user_response], manager.telegram, manager.state)
                    break
                elif tdata.get("status") == "WAITING_APPROVAL":
                    handled = True
                    if "aprob" in user_response.lower() or "si" in user_response.lower():
                        action_type = tdata.get("error_msg")
                        manager.state.save_step(tid, f"approval_{action_type}", {"approved": True})
                        manager.state.update_task_status(tid, "RUNNING")
                        manager.telegram.send_message("✅ Aprobado. Reanudando habilidad...")
                        proposal = manager.state.get_step_data(tid, "compilation")
                        if proposal and "file" in proposal:
                            background_tasks.add_task(manager.forge.execute_skill, tid, proposal["file"], tdata["request"], manager.telegram, manager.state)
                    else:
                        step_data = manager.state.get_step_data(tid, "compilation")
                        previous_code = step_data.get("code", "") if step_data else ""
                        request_text = tdata.get("request", "")
                        
                        manager.telegram.send_message("❌ Permiso denegado. Devolviendo código a Khai para correcciones basándome en tu feedback...")
                        import asyncio
                        asyncio.create_task(asyncio.to_thread(
                            manager.forge.propose_skill, 
                            tid, request_text, manager.telegram, manager.state, 
                            previous_code=previous_code, feedback=user_response,
                            target_file=step_data.get("file", "") if step_data else None
                        ))
                    break
                    
            if not handled:
                manager.telegram.send_message(text="No hay tareas esperando tu entrada.")
                
        else:
            # GENERAL_CHAT: Khai responde nativamente.
            if reply: manager.telegram.send_message(text=reply)
        
    return {"ok": True}

@app.on_event("startup")
async def startup_event():
    manager.telegram.send_message(text="✅ Sistema reiniciado. Conectado a SQLite.")
    logger.info("Orquestador iniciado (FastAPI). Máquina de estados SQLite lista. Corazón Latiendo.")
    import asyncio
    asyncio.create_task(manager.core_heart.start_beating())
    
    # Reanudar tareas RUNNING que se cayeron
    pending = manager.state.get_pending_tasks()
    running_count = 0
    for tid, tdata in pending.items():
        if tdata.get("status") == "RUNNING":
            proposal = manager.state.get_step_data(tid, "compilation")
            if proposal and "file" in proposal:
                running_count += 1
                asyncio.create_task(asyncio.to_thread(
                    manager.forge.execute_skill, 
                    tid, proposal["file"], tdata["request"], manager.telegram, manager.state
                ))
    if running_count > 0:
        logger.info(f"Reanudadas {running_count} tareas desde la cola de SQLite.")
        manager.telegram.send_message(text=f"🔄 Reanudadas {running_count} tareas en background desde SQLite.")

if __name__ == "__main__":
    cert_file = os.path.abspath("data/certs/cert.pem")
    key_file = os.path.abspath("data/certs/key.pem")
    
    if os.path.exists(cert_file) and os.path.exists(key_file):
        logger.info(f"Iniciando Uvicorn con HTTPS en puerto 8443... (Certs encontrados en {os.path.dirname(cert_file)})")
        uvicorn.run(app, host="0.0.0.0", port=8443, ssl_keyfile=key_file, ssl_certfile=cert_file)
    else:
        logger.warning("⚠️ No se encontraron certificados SSL. Iniciando Uvicorn en HTTP (Puerto 8000) por defecto.")
        uvicorn.run(app, host="0.0.0.0", port=8000)
