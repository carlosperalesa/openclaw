import os
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable

from modules.state_manager import StateManager
from modules.libs.telegram_handler import TelegramHandler

logger = logging.getLogger("SkillForge")


class ApprovalRequiredException(Exception):
    """Excepción lanzada cuando una Skill alcanza un punto que requiere permiso manual."""

    def __init__(self, action_type: str, message: str):
        self.action_type = action_type
        self.message = message
        super().__init__(self.message)


class BaseSkill(ABC):
    """
    Clase base para todas las habilidades (Skills) dinámicas creadas por Khai.
    Impone la memoria transaccional y las reglas de seguridad.
    """

    def __init__(self, task_id: str, state_manager: StateManager, telegram: TelegramHandler):
        self.task_id = task_id
        self.state = state_manager
        self.telegram = telegram

    def run_step(self, step_name: str, func: Callable, *args, **kwargs) -> Any:
        if self.state.is_step_completed(self.task_id, step_name):
            logger.info(
                f"[Task {self.task_id}] Paso '{step_name}' ya completado. Usando caché.")
            return self.state.get_step_data(self.task_id, step_name)

        logger.info(f"[Task {self.task_id}] Ejecutando paso: {step_name}...")
        try:
            result = func(*args, **kwargs)
            self.state.save_step(self.task_id, step_name, result)
            return result
        except Exception as e:
            logger.error(
                f"[Task {self.task_id}] Error en paso '{step_name}': {e}")
            raise e

    def require_approval(self, action_type: str, details: str):
        approval_step_name = f"approval_{action_type}"
        if self.state.is_step_completed(self.task_id, approval_step_name):
            return

        raise ApprovalRequiredException(
            action_type=action_type,
            message=f"🛑 <b>Permiso Requerido:</b> La habilidad intenta ejecutar '{action_type}'.\n\nDetalles: {details}\n\nResponde 'Aprobar' o 'Denegar'."
        )

    def mark_approved(self, action_type: str):
        approval_step_name = f"approval_{action_type}"
        self.state.save_step(
            self.task_id, approval_step_name, {"approved": True})

    @abstractmethod
    def execute(self, request: str):
        pass


class ProgressTracker:
    """
    Barra de progreso visual en Telegram.
    Envía un mensaje al inicio y lo edita en cada paso del pipeline.
    """
    
    STEPS = [
        ("🔍", "Buscando en librería RAG"),
        ("🧠", "Generando código"),
        ("🛡️", "Revisión de seguridad (Pixy)"),
        ("⚡", "Ejecutando en sandbox"),
    ]
    
    def __init__(self, telegram: 'TelegramHandler', task_id: str):
        self.telegram = telegram
        self.task_id = task_id
        self.message_id = None
        self.current_step = -1
        self.total = len(self.STEPS)
        self.error = False
        
    def start(self):
        """Envía el mensaje inicial y guarda el message_id."""
        text = self._render(step_index=0, status="active")
        result = self.telegram.send_message(text=text)
        if result and result.get("ok") and result.get("result", {}).get("message_id"):
            self.message_id = result["result"]["message_id"]
        self.current_step = 0
        
    def advance(self, step_index: int = None, detail: str = None):
        """Avanza al siguiente paso o a un paso específico."""
        if step_index is not None:
            self.current_step = step_index
        else:
            self.current_step += 1
        self._update(detail)
        
    def complete(self, success: bool = True, detail: str = None):
        """Marca el pipeline como completado o fallido."""
        self.error = not success
        self.current_step = self.total
        self._update(detail, final=True)
    
    def _update(self, detail: str = None, final: bool = False):
        """Edita el mensaje con el estado actual."""
        if not self.message_id:
            return
        text = self._render(self.current_step, "done" if final else "active", detail, final)
        self.telegram.edit_message(self.message_id, text)
    
    def _render(self, step_index: int, status: str, detail: str = None, final: bool = False) -> str:
        """Renderiza la barra de progreso."""
        if final and not self.error:
            header = f"✅ <b>Pipeline Completado</b> — <code>{self.task_id}</code>"
        elif final and self.error:
            header = f"❌ <b>Pipeline Fallido</b> — <code>{self.task_id}</code>"
        else:
            header = f"⚙️ <b>Skill Pipeline</b> — <code>{self.task_id}</code>"
        
        lines = [header, ""]
        
        for i, (emoji, label) in enumerate(self.STEPS):
            if final and not self.error:
                lines.append(f"  ✅ {label}")
            elif i < step_index:
                lines.append(f"  ✅ {label}")
            elif i == step_index and not final:
                lines.append(f"  {emoji} <b>{label}...</b>")
            else:
                lines.append(f"  ⬜ {label}")
        
        # Barra visual
        if final:
            filled = self.total
        else:
            filled = step_index
        bar_len = 12
        fill = round(bar_len * filled / self.total)
        bar = "▓" * fill + "░" * (bar_len - fill)
        pct = round(100 * filled / self.total)
        lines.append(f"\n<code>[{bar}]</code> {pct}%")
        
        if detail:
            lines.append(f"\n💬 <i>{detail}</i>")
        
        return "\n".join(lines)


class SkillForge:
    """
    Forjador de habilidades dinámicas. Analiza riesgos, negocia permisos,
    escribe código heredado de BaseSkill y lo ejecuta dinámicamente.
    """

    def __init__(self, llm, data_dir: str = "data/skills"):
        self.llm = llm
        self.data_dir = data_dir
        self._trackers = {}  # task_id -> ProgressTracker
        os.makedirs(self.data_dir, exist_ok=True)

    def _get_tracker(self, task_id: str, telegram: TelegramHandler) -> ProgressTracker:
        """Obtiene o crea un tracker para un task_id."""
        if task_id not in self._trackers:
            tracker = ProgressTracker(telegram, task_id)
            tracker.start()
            self._trackers[task_id] = tracker
        return self._trackers[task_id]
    
    def _cleanup_tracker(self, task_id: str):
        """Elimina el tracker al finalizar."""
        self._trackers.pop(task_id, None)

    def resolve_request(self, task_id: str, request: str, telegram: TelegramHandler, state: StateManager, hypothesis_id: str = None, alternative_hypotheses: list = None):
        """Paso 1: Busca en la librería RAG si la skill ya existe, necesita upgrade o es nueva."""
        tracker = self._get_tracker(task_id, telegram)
        skills = state.get_all_skills()
        if skills:
            logger.info(
                f"[Task {task_id}] Buscando en RAG ({len(skills)} skills disponibles)...")
            prompt = f"""
            El usuario pidió: "{request}"
            
            Tienes las siguientes habilidades ACTIVAS pre-existentes en tu librería:
            {json.dumps(skills, indent=2)}
            
            Evalúa la librería y decide basándote ESTRICTAMENTE en la instrucción recibida:
            1. REGLA ABSOLUTA: Si la instrucción te ordena explícitamente actualizar, cambiar de estrategia, arreglar un error, o eliminar una dependencia de una habilidad previa, TIENES PROHIBIDO responder USE. En este caso ESTÁS OBLIGADO a responder: UPGRADE: nombre_de_familia (si debes mejorar la existente) o CREATE (si debes hacer una estrategia nueva desde cero).
            2. Si la instrucción es una petición normal y una habilidad lo resuelve EXACTAMENTE sin requerir cambios, responde: USE: nombre_de_familia
            3. Si ninguna habilidad sirve y hay que crear algo totalmente nuevo, responde: CREATE
            4. Si el usuario pide explícitamente eliminar o desactivar una habilidad, responde: DELETE: nombre_de_familia
            
            Devuelve ÚNICAMENTE la palabra clave (ej. USE: youtube_downloader, CREATE, o DELETE: nombre_de_familia).
            """
            try:
                action = self.llm.generate_content(prompt).strip()
                logger.info(f"[Task {task_id}] Acción RAG: {action}")

                if action.startswith("USE:"):
                    fam = action.split(":", 1)[1].strip()
                    matched_skill = next(
                        (s for s in skills if s["name"] == fam), None)
                    if matched_skill:
                        logger.info(
                            f"[Task {task_id}] RAG Hit: Reutilizando {matched_skill['name']}")
                        tracker.advance(
                            step_index=1, detail=f"Habilidad '{fam}' encontrada en caché.")

                        # Ejecutar sin repensar, directo al docker_proxy
                        state.create_task(task_id, fam, request, hypothesis_id=hypothesis_id, alternative_hypotheses=alternative_hypotheses)
                        
                        # Extraer dependencias del archivo original
                        deps = []
                        with open(matched_skill["file_path"], "r", encoding="utf-8") as f:
                            for line in f:
                                if line.startswith("# PIP:"):
                                    deps = line.replace(
                                        "# PIP:", "").strip().split()
                                    break

                        self.execute_skill(
                            task_id, matched_skill["file_path"], request, telegram, state, deps)
                        return

                if action.startswith("UPGRADE:"):
                    fam = action.split(":", 1)[1].strip()
                    matched_skill = next(
                        (s for s in skills if s["name"] == fam), None)
                    if matched_skill:
                        logger.info(
                            f"[Task {task_id}] RAG UPGRADE: Mejorando {matched_skill['name']}")
                        tracker.advance(
                            step_index=1, detail=f"Actualizando habilidad '{fam}'...")

                        # Leer el código viejo
                        try:
                            with open(matched_skill["file_path"], "r", encoding="utf-8") as f:
                                old_code = f.read()
                            self.propose_skill(
                                task_id, request, telegram, state, previous_code=old_code, upgrade_family=fam, target_file=matched_skill["file_path"], hypothesis_id=hypothesis_id, alternative_hypotheses=alternative_hypotheses)
                            return
                        except Exception as ex:
                            logger.error(
                                f"No se pudo leer archivo viejo para upgrade: {ex}")

                if action.startswith("DELETE:"):
                    fam = action.split(":", 1)[1].strip()
                    matched_skill = next(
                        (s for s in skills if s["name"] == fam), None)
                    if matched_skill:
                        logger.info(
                            f"[Task {task_id}] RAG DELETE: Eliminando {matched_skill['name']}")

                        # Borrar todos los archivos físicos de esta familia
                        import glob
                        for f in glob.glob(os.path.join(self.data_dir, f"{fam}_*.py")):
                            try:
                                os.remove(f)
                            except Exception as ex:
                                logger.error(
                                    f"Error borrando archivo de skill {f}: {ex}")

                        # Borrar de SQLite
                        state.delete_skill(fam)
                        telegram.send_message(
                            f"🗑️ <b>Khai (Mantenimiento):</b> La habilidad `{fam}` ha sido eliminada permanentemente del sistema.")

                        state.create_task(task_id, fam, request, hypothesis_id=hypothesis_id, alternative_hypotheses=alternative_hypotheses)
                        state.update_task_status(task_id, "COMPLETED")

                        # Guardar evento
                        if hasattr(self, "manager") and self.manager:
                            self.manager.memory.append_long_term(
                                "system_msg", f"[RESULTADO EJECUCION TASK {task_id}]: Habilidad {fam} eliminada con éxito.")
                        return

            except Exception as e:
                logger.error(f"Error en RAG: {e}")

        # Si CREATE o falló el RAG
        tracker.advance(step_index=1, detail="Creando nueva habilidad...")
        self.propose_skill(task_id, request, telegram, state, hypothesis_id=hypothesis_id, alternative_hypotheses=alternative_hypotheses)

    def propose_skill(self, task_id: str, request: str, telegram: TelegramHandler, state: StateManager, previous_code: str = None, feedback: str = None, upgrade_family: str = None, target_file: str = None, hypothesis_id: str = None, alternative_hypotheses: list = None):
        """Paso 2 y 3: Khai busca librerías externas, edita o escribe el código desde cero."""
        tracker = self._get_tracker(task_id, telegram)
        tracker.advance(step_index=1, detail="Khai está escribiendo código...")
        if feedback:
            logger.info(
                f"[Task {task_id}] Reproponiendo skill con feedback: {feedback}")
            telegram.send_message(
                f"⚙️ <b>Khai:</b> He recibido tus correcciones. Estoy reescribiendo el código...")

            prompt = f"""
            Actúa como un Programador Python Senior.
            Se te asignó el siguiente objetivo original: "{request}"
            
            Escribiste este código:
            ```python
            {previous_code}
            ```
            
            Tu supervisor de seguridad RECHAZÓ tu código con esta corrección obligatoria:
            "CRÍTICA: {feedback}"
            
            OBLIGATORIO: Antes de escribir el código, DEBES incluir un bloque de monólogo interno así:
            [THOUGHT]
            (Analiza por qué falló y cómo lo arreglarás)
            [/THOUGHT]
            
            Reescribe el código aplicando estrictamente la corrección. Mantén los headers de `# MODE:` y `# PIP:` si aplican.
            """
        elif upgrade_family:
            logger.info(
                f"[Task {task_id}] Haciendo Upgrade a {upgrade_family}")
            telegram.send_message(
                f"⚙️ <b>Khai:</b> Estoy generando una nueva versión de la habilidad `{upgrade_family}` para añadir lo que me pediste...")

            prompt = f"""
            Actúa como un Programador Python Senior.
            El usuario solicitó lo siguiente: "{request}"
            
            Actualmente tenemos esta versión funcional de la habilidad `{upgrade_family}`:
            ```python
            {previous_code}
            ```
            
            TU TAREA:
            Modifica y mejora el código anterior para que cumpla con el nuevo requerimiento del usuario. 
            MANTÉN la primera línea `# MODE:` igual que antes, y mantén o actualiza las dependencias `# PIP:` en la segunda línea si las necesita.
            
            OBLIGATORIO: Antes de escribir el código, DEBES incluir un bloque de monólogo interno así:
            [THOUGHT]
            (Analiza qué vas a cambiar)
            [/THOUGHT]
            """
        else:
            logger.info(
                f"[Task {task_id}] Proponiendo nueva skill para: {request}")
            telegram.send_message(
                f"⚙️ <b>Khai:</b> Dejame pensar en tu solicitud...")

            prompt = f"""
            Actúa como un Programador Python Senior.
            Debes escribir un script de Python para cumplir este objetivo: "{request}"
            
            REGLAS ESTRICTAS DE CABECERAS:
            1. La PRIMERA LÍNEA de tu código DEBE indicar el modo de ejecución:
               - `# MODE: SAFE` (Por defecto. Para scripts de ejecución rápida y corta, ej. scraping, reportes).
               - `# MODE: AGENT` (Para procesos de larga duración en background, servidores web, bots secundarios. Corre como un daemon).
               - `# MODE: GOD` (Solo para administrar el servidor VPS, modificar archivos core o docker nativo. NO requiere PIP).
            
            2. La SEGUNDA LÍNEA de tu código puede ser opcionalmente dependencias externas:
               - Si existe alguna librería en PIP/Internet que solucione el problema fácilmente sin tener que reinventar la rueda (Paso 2), debes declararla así: `# PIP: nombre_libreria1 nombre_libreria2`.
               - Si puedes resolverlo con pura lógica o librerías nativas, no escribas la línea # PIP, simplemente escribe tu código desde cero (Paso 3).
               - IMPORTANTE: Si usas `# PIP:`, estás OBLIGADO a usar `# MODE: SAFE` o `# MODE: AGENT`. El God Mode tiene prohibido usar PIP dinámico.
            
            Si usas `# MODE: SAFE`:
            El código se ejecutará aislado. No tienes acceso a BaseSkill. Imprime tu resultado final con `print()`.
            Ejemplo:
            ```python
            # MODE: SAFE
            # PIP: bs4 requests
            import requests
            from bs4 import BeautifulSoup
            print("Scraping completado...")
            ```
            
            Si usas `# MODE: GOD`:
            Deberás crear una clase `DynamicSkill` que herede de `BaseSkill`. NO PUEDES USAR `# PIP:`.
            Ejemplo:
            ```python
            # MODE: GOD
            from modules.skill_forge import BaseSkill
            class DynamicSkill(BaseSkill):
                def execute(self, request: str):
                    return "Administración completada"
            ```
            
            Si usas `# MODE: AGENT`:
            El código se ejecutará como un servicio daemon en background continuo. Usa esto para levantar servidores, APIs o bots que nunca mueren.
            Ejemplo:
            ```python
            # MODE: AGENT
            # PIP: fastapi uvicorn
            from fastapi import FastAPI
            import uvicorn
            app = FastAPI()
            @app.get("/")
            def read_root(): return {{"status": "ok"}}
            if __name__ == "__main__":
                uvicorn.run(app, host="0.0.0.0", port=8080)
            ```
            
            OBLIGATORIO: Antes de escribir el código, DEBES incluir un bloque de monólogo interno así:
            [THOUGHT]
            (Analiza el problema y cómo lo vas a resolver)
            [/THOUGHT]
            
            Luego, devuelve el código Python en un bloque ```python ... ```.
            """

        # --- Extracción JIT de Guidelines ---
        import re
        import json
        tags = ["general"]
        match = re.search(r"\[MANIFEST\]\s*(\{.*?\})", request, re.DOTALL)
        if match:
            try:
                manifest_data = json.loads(match.group(1))
                tags.extend(manifest_data.get("capabilities", []))
                purpose = manifest_data.get("purpose", "")
                tags.extend([w.lower() for w in purpose.split() if len(w) > 4])
            except Exception:
                pass
                
        if hasattr(self, "manager") and self.manager:
            guidelines = self.manager.memory.get_guidelines_by_tags(tags)
            if guidelines:
                guidelines_text = "\n".join([f"- {g['rule_text']}" for g in guidelines])
                prompt += f"\n\nREGLAS DE ARQUITECTURA APRENDIDAS (Aplica estrictamente):\n{guidelines_text}\n"

        try:
            raw_response = self.llm.generate_content(prompt)

            thought = ""
            if "[THOUGHT]" in raw_response and "[/THOUGHT]" in raw_response:
                import re
                match = re.search(
                    r"\[THOUGHT\](.*?)\[/THOUGHT\]", raw_response, re.DOTALL)
                if match:
                    thought = match.group(1).strip()
                    telegram.send_message(
                        f"💭 <i>Pensamiento de Khai:</i>\n{thought}")

            code = raw_response
            if "```python" in raw_response:
                code = raw_response.split("```python")[1].split("```")[0]
            elif "```" in raw_response:
                code = raw_response.split("```")[1]
            elif "[/THOUGHT]" in raw_response:
                code = raw_response.split("[/THOUGHT]")[1]

            # Limpiar residuos de [THOUGHT] que el LLM a veces mete dentro del bloque de código
            import re
            code = re.sub(r'\[THOUGHT\].*?\[/THOUGHT\]', '', code, flags=re.DOTALL)
            code = code.strip()

            # Nombrado Inteligente de Versiones
            new_v = None
            if target_file:
                file_path = target_file
                fam_name = os.path.basename(file_path).split('_v')[0]
            else:
                fam_name = upgrade_family if upgrade_family else f"skill_{task_id.split('_')[1]}"
                new_v = state.get_latest_version(fam_name) + 1
                file_path = os.path.join(
                    self.data_dir, f"{fam_name}_v{new_v}.py")

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)

            state.create_task(task_id, fam_name, request, hypothesis_id=hypothesis_id, alternative_hypotheses=alternative_hypotheses)
            state.save_step(task_id, "compilation", {
                            "file": file_path, "code": code})
            state.update_task_status(task_id, "WAITING_PIXY")
            if hasattr(self, "manager") and self.manager:
                self.manager.memory.append_long_term(
                    "code_generated",
                    f"[CODE_GENERATED TASK {task_id}]: {fam_name} -> {file_path}",
                    metadata={"task_id": task_id, "skill_name": fam_name, "file_path": file_path},
                )

            version_label = f"v{new_v}" if new_v is not None else os.path.basename(
                file_path)
            tracker.advance(step_index=2, detail=f"Código {version_label} listo. Pixy revisando...")

            if hasattr(self, "manager") and self.manager:
                self.manager.pixy.process_request(
                    "Khai acaba de generar el código para la tarea. Por favor revísala y actívala si es segura.", is_automated=True)

        except Exception as e:
            logger.error(f"Error proponiendo skill: {e}")
            telegram.send_message(
                f"🫀 <b>CoreHeart (Exception Caught)</b>\n<b>Location:</b> SkillForge.propose_skill\n<b>Error:</b> <code>{e}</code>")

    def execute_skill(self, task_id: str, file_path: str, request: str, telegram: TelegramHandler, state: StateManager, deps: list = None):
        """Paso 3: Lanza la skill solicitando ejecución al Proxy de Docker."""
        tracker = self._get_tracker(task_id, telegram)
        tracker.advance(step_index=3, detail="Ejecutando en contenedor aislado...")
        
        def trigger_auto_reflexive_loop(final_output: str, is_error: bool = False):
            if hasattr(self, "manager") and self.manager:
                status_label = "ERROR DEFINITIVO" if is_error else "EXITO"
                self.manager.memory.append_long_term("system_msg", f"[RESULTADO EJECUCION TASK {task_id}]: {status_label}\n{final_output}")
                
                # --- ACTUALIZAR CONFIANZA ESTADÍSTICA DE HIPÓTESIS ---
                task_data = state.get_task(task_id)
                if task_data and task_data.get("hypothesis_id"):
                    hyp_id = task_data.get("hypothesis_id")
                    context = task_data.get("skill_name") or "general"
                    self.manager.memory.update_hypothesis_metrics(hyp_id, success=not is_error, context=context)

                if is_error and task_data:
                    next_hypothesis_id, remaining = state.promote_next_hypothesis(task_id)
                    if next_hypothesis_id:
                        hypothesis = self.manager.memory.get_hypothesis(next_hypothesis_id)
                        hypothesis_content = hypothesis.get("content", "") if hypothesis else ""
                        fallback_request = (
                            f"{request}\n\n"
                            "[SYSTEM_EVENT] La hipótesis causal activa falló empíricamente. "
                            "NO solicites un nuevo análisis desde cero. Reintenta la misma tarea "
                            "usando esta hipótesis alternativa como estrategia principal:\n"
                            f"[HYPOTHESIS #{next_hypothesis_id}]\n{hypothesis_content}\n\n"
                            f"Output fallido anterior:\n{final_output}"
                        )
                        self.manager.memory.append_long_term(
                            "system_msg",
                            f"[HYPOTHESIS_FALLBACK TASK {task_id}]: promovida #{next_hypothesis_id}. Restantes: {remaining}",
                            metadata={"task_id": task_id, "hypothesis_id": next_hypothesis_id, "remaining": remaining},
                        )
                        telegram.send_message(
                            f"🧪 <b>Khai (Fallback Cognitivo):</b> La hipótesis activa falló. Promuevo <code>#{next_hypothesis_id}</code> y relanzo sin reanalizar desde cero."
                        )
                        self.resolve_request(
                            task_id,
                            fallback_request,
                            telegram,
                            state,
                            hypothesis_id=next_hypothesis_id,
                            alternative_hypotheses=remaining,
                        )
                        return
                    
                telegram.send_message(f"🧠 <b>Khai (Auto-Reflexión):</b> Evaluando resultados empíricos...")
                
                sys_prompt = f"[SYSTEM_EVENT] Resultado de Skill task_{task_id}:\n{final_output}\nEvalúa proactivamente este resultado. Si la tarea se completó con éxito y obtuvo la información pedida, responde al usuario entregándole la info. Si el output es un mensaje de error o fallo encubierto, NO respondas al usuario; en su lugar formula una hipótesis de qué falló y genera una orden de ACTION_REQUIRED con un extracted_goal exigiendo a la forja un UPGRADE o CREATE cambiando de estrategia (ej. no usar la API fallida, usar otra librería)."
                
                import threading
                def khais_loop():
                    intent = self.manager.core_mind.process_message(sys_prompt)
                    action = intent.get("action")
                    reply = intent.get("reply")
                    extracted_goal = intent.get("extracted_goal")
                    
                    if reply:
                        telegram.send_message(text=reply)
                    
                    if action == "ACTION_REQUIRED" and extracted_goal:
                        import uuid
                        new_task_id = f"task_{uuid.uuid4().hex[:8]}"
                        self.resolve_request(new_task_id, extracted_goal, telegram, state)
                        
                threading.Thread(target=khais_loop).start()
            else:
                telegram.send_message(f"✅ Skill Completada (Output crudo):\n\n{final_output}")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                first_line = f.readline().strip().upper()

            import os
            import requests

            if "MODE: GOD" in first_line:
                mode = "GOD"
            elif "MODE: AGENT" in first_line:
                mode = "AGENT"
            else:
                mode = "SAFE"

            logger.info(
                f"[Task {task_id}] Solicitando ejecución al Proxy en modo {mode}.")
            if mode == "SAFE":
                telegram.send_message(
                    f"🔒 Ejecutando en Modo Seguro (Worker Sandboxed)...")
            elif mode == "AGENT":
                telegram.send_message(
                    f"🚀 Ejecutando en Modo Agente (Daemon Background)...")
            else:
                telegram.send_message(f"⚠️ Ejecutando en Modo GOD...")

            if mode == "AGENT":
                proxy_url = os.environ.get(
                    "PROXY_URL", "http://docker_proxy:8001/run-skill").replace("/run-skill", "/spawn-agent")
            else:
                proxy_url = os.environ.get(
                    "PROXY_URL", "http://docker_proxy:8001/run-skill")

            payload = {
                "task_id": task_id,
                # ruta relativa o basename dependiendo de cómo esté guardado, file_path en BD es data/skills/...
                "file_path": file_path,
                "request": request,
                "mode": mode,
                "dependencies": deps or []
            }

            response = requests.post(proxy_url, json=payload, timeout=130)
            response.raise_for_status()

            result_data = response.json()
            output = result_data.get("stdout", "").strip()
            errors = result_data.get("stderr", "").strip()
            returncode = result_data.get("returncode", 1)

            if mode == "SAFE":
                if returncode == 0:
                    state.update_task_status(task_id, "COMPLETED")
                    tracker.complete(success=True, detail="Skill ejecutada exitosamente")
                    self._cleanup_tracker(task_id)
                    trigger_auto_reflexive_loop(output, is_error=False)
                else:
                    attempts = state.get_task_attempts(task_id)
                    if attempts < 3:
                        state.increment_task_attempts(task_id)
                        tracker.complete(success=False, detail=f"Error (intento {attempts+1}/3). Reintentando...")
                        telegram.send_message(
                            f"♻️ <b>Khai (Auto-Corrección {attempts+1}/3):</b> El código falló en Safe Mode. Analizando traceback y reprogramando...")
                        with open(file_path, "r", encoding="utf-8") as f:
                            prev_code = f.read()
                        self.propose_skill(task_id, request, telegram, state, previous_code=prev_code,
                                           feedback=f"Traceback/Error:\n{errors or output}")
                    else:
                        state.update_task_status(task_id, "ERROR", errors)
                        tracker.complete(success=False, detail="Error definitivo tras 3 intentos")
                        self._cleanup_tracker(task_id)
                        trigger_auto_reflexive_loop(errors, is_error=True)
            else:
                if returncode == 0 and "RESULTADO_OK:" in output:
                    final_res = output.split("RESULTADO_OK:")[-1].strip()
                    state.update_task_status(task_id, "COMPLETED")
                    tracker.complete(success=True, detail="Skill ejecutada exitosamente")
                    self._cleanup_tracker(task_id)
                    trigger_auto_reflexive_loop(final_res, is_error=False)
                else:
                    attempts = state.get_task_attempts(task_id)
                    if attempts < 3:
                        state.increment_task_attempts(task_id)
                        tracker.complete(success=False, detail=f"Error (intento {attempts+1}/3). Reintentando...")
                        telegram.send_message(
                            f"♻️ <b>Khai (Auto-Corrección {attempts+1}/3):</b> El código falló en God Mode. Analizando traceback y reprogramando...")
                        with open(file_path, "r", encoding="utf-8") as f:
                            prev_code = f.read()
                        self.propose_skill(task_id, request, telegram, state, previous_code=prev_code,
                                           feedback=f"Traceback/Error:\n{errors or output}")
                    else:
                        state.update_task_status(
                            task_id, "ERROR", errors or output)
                        tracker.complete(success=False, detail="Error definitivo tras 3 intentos")
                        self._cleanup_tracker(task_id)
                        trigger_auto_reflexive_loop(errors or output, is_error=True)

            # Guardamos la skill en la librería permanentemente
            task_data = state.get_task(task_id)
            if task_data and task_data.get("status") == "COMPLETED":
                # Si se completó sin errores (no está en pending)
                if "MODE: GOD" in first_line:
                    final_mode = "GOD"
                elif "MODE: AGENT" in first_line:
                    final_mode = "AGENT"
                else:
                    final_mode = "SAFE"
                state.save_to_library(
                    task_data["skill_name"], request, file_path, final_mode)

        except ApprovalRequiredException as e:
            state.update_task_status(
                task_id, "WAITING_APPROVAL", e.action_type)
            tracker.advance(step_index=2, detail="Esperando aprobación del usuario...")
            telegram.send_message(e.message)

        except Exception as e:
            logger.error(f"Error crítico en ejecución de skill: {e}")
            tracker.complete(success=False, detail=str(e))
            self._cleanup_tracker(task_id)
            state.update_task_status(task_id, "ERROR", str(e))
            telegram.send_message(
                f"❌ <b>Error Crítico en ejecución de Skill</b>\n{e}")
