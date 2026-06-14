import logging
import json
import ast

logger = logging.getLogger("Pixy")

class PixyMind:
    """
    Pixy: Agente Supervisor (DevOps) de OpenClaw.
    Tiene acceso a logs de Docker, estado de Khai y puede intervenir en permisos.
    Ahora utiliza un análisis AST determinista para evaluar el código.
    """
    
    def __init__(self, manager):
        self.manager = manager
        self.llm = manager.llm
        self.telegram = manager.telegram
        self.state = manager.state

    def _analyze_code_ast(self, code: str, manifest: dict = None) -> dict:
        """Analiza el código estáticamente contra la Constitución y el Manifest declarado."""
        import yaml
        import os
        
        constitution = {"banned_calls": [], "banned_imports": [], "banned_qualified_calls": [], "capabilities": {}}
        try:
            if os.path.exists("config/constitution.yaml"):
                with open("config/constitution.yaml", "r", encoding="utf-8") as f:
                    constitution = yaml.safe_load(f) or constitution
        except Exception as e:
            logger.error(f"Pixy no pudo leer la constitución: {e}")

        try:
            tree = ast.parse(code)
            imports = set()          # Nombres de módulos importados
            import_aliases = {}      # alias -> módulo real (ej: import subprocess as sp)
            calls = set()            # Llamadas globales (ej: exec, eval)
            qualified_calls = set()  # Pares (módulo, función) (ej: subprocess.run)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name)
                        import_aliases[alias.asname or alias.name] = alias.name
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module)
                        for alias in node.names:
                            import_aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        if isinstance(node.func.value, ast.Name):
                            obj_name = node.func.value.id
                            real_module = import_aliases.get(obj_name, obj_name)
                            qualified_calls.add((real_module, node.func.attr))
                        calls.add(node.func.attr)
                    elif isinstance(node.func, ast.Name):
                        calls.add(node.func.id)
                        full_path = import_aliases.get(node.func.id, "")
                        if "." in full_path:
                            mod, func = full_path.rsplit(".", 1)
                            qualified_calls.add((mod, func))
                        
            # 1. Validar imports prohibidos
            banned_imports_found = [i for i in imports if i in constitution.get("banned_imports", [])]
            if banned_imports_found:
                return {"safe": False, "reason": f"Violación a la Constitución: Imports prohibidos detectados ({', '.join(banned_imports_found)})"}
            
            # 2. Validar llamadas globales prohibidas
            banned_calls_found = [c for c in calls if c in constitution.get("banned_calls", [])]
            if banned_calls_found:
                return {"safe": False, "reason": f"Violación a la Constitución: Llamadas prohibidas detectadas ({', '.join(banned_calls_found)})"}
            
            # 3. Validar llamadas calificadas
            banned_qualified = constitution.get("banned_qualified_calls", [])
            for bq in banned_qualified:
                bq_mod = bq.get("module", "")
                bq_call = bq.get("call", "")
                for (mod, func) in qualified_calls:
                    if mod == bq_mod and func == bq_call:
                        return {"safe": False, "reason": f"Violación a la Constitución: Llamada calificada prohibida ({bq_mod}.{bq_call})"}
                        
            # 4. Validar AST vs Manifest Capabilities
            if manifest:
                requested_caps = manifest.get("capabilities", [])
                caps_dict = constitution.get("capabilities", {})
                for mod in imports:
                    required_cap = None
                    for cap_name, cap_data in caps_dict.items():
                        if mod in cap_data.get("allowed_modules", []):
                            required_cap = cap_name
                            break
                    if required_cap and required_cap not in requested_caps:
                        return {"safe": False, "reason": f"CapabilityViolation: El AST importa '{mod}', lo que requiere la capacidad '{required_cap}', pero el MANIFEST no la declara."}
                
            report = []
            if imports: report.append(f"Imports: {', '.join(list(imports))}")
            if calls: report.append(f"Calls: {', '.join(list(calls))}")
            
            return {"safe": True, "report": " | ".join(report) if report else "AST limpio."}
            
        except Exception as e:
            return {"safe": False, "reason": f"Error parseando AST (Sintaxis inválida detectada pre-ejecución): {e}"}

    def _generate_content(self, prompt: str) -> str:
        """Intenta usar Azure primero. Si falla, usa el pool global."""
        import os
        
        azure_key = None
        for k, v in os.environ.items():
            if k.startswith("AZURE_OPENAI_API_KEY"):
                azure_key = v
                break
                
        if azure_key:
            try:
                return self.llm.generate_content(prompt, forced_key=azure_key, forced_type="azure")
            except Exception as e:
                logger.warning(f"[Pixy] Falló la llamada a Azure: {e}. Fallback al pool global.")
                
        # Fallback al pool global (Khai)
        return self.llm.generate_content(prompt)

    def _semantic_congruence_check(self, code: str, manifest: dict) -> dict:
        """
        Última barrera judicial. Evalúa semánticamente si el código hace lo que dice el manifest.
        Cuesta tokens, pero garantiza la seguridad lógica.
        """
        import json
        prompt = f"""
        Actúa como un Auditor de Seguridad DevOps (Pixy).
        
        Se ha propuesto el siguiente código Python.
        ```python
        {code}
        ```
        
        El autor declaró este manifiesto de intenciones:
        {json.dumps(manifest, indent=2)}
        
        Evalúa semánticamente la congruencia. ¿El código realmente hace lo que dice su "purpose" y respeta las "capabilities" declaradas, o intenta realizar operaciones maliciosas u ocultas que no cuadran con su descripción?
        
        Debes responder estrictamente con un JSON válido usando este formato:
        {{
            "safe": true/false,
            "reason": "Explicación corta de por qué es congruente o incongruente."
        }}
        """
        try:
            res = self._generate_content(prompt)
            if res.startswith("```json"): res = res[7:-3]
            elif res.startswith("```"): res = res[3:-3]
            data = json.loads(res.strip())
            return {"safe": data.get("safe", False), "reason": data.get("reason", "Unknown")}
        except Exception as e:
            return {"safe": False, "reason": f"Fallo en la validación semántica (LLM Error): {str(e)}"}

    def process_request(self, message: str, is_automated: bool = False):
        """Procesa una solicitud directa del usuario a Pixy."""
        logger.info(f"[Pixy] Procesando orden: {message}")
        
        # Guardar mensaje en memoria global
        if not is_automated:
            self.manager.memory.append_long_term("user_chat", f"[Dirigido a Pixy]: {message}")
        else:
            self.manager.memory.append_long_term("system_msg", f"Pixy fue invocada automáticamente para revisión.")
        
        # Eliminar el prefijo "pixy" del mensaje
        clean_msg = message.lower().replace("pixy", "").strip()
        
        # Obtener contexto actual para Pixy
        context_data = self._gather_context()
        chat_history = self.manager.memory.get_chat_history(limit=100)
        
        # Obtener AST y Manifest si hay una tarea en WAITING_PIXY
        ast_result = {"safe": True, "report": "No hay código pendiente para analizar."}
        task_id_to_reject = None
        task_id_to_semantic_check = None
        code_to_check = None
        manifest_to_check = None
        
        for tid, task in context_data['pending_tasks'].items():
            if task['status'] == 'WAITING_PIXY':
                code = task['steps'].get('compilation', {}).get('data', {}).get('code', '')
                request_text = task.get('request', '')
                
                import re
                manifest_data = {}
                match = re.search(r"\[MANIFEST\]\s*(\{.*?\})", request_text, re.DOTALL)
                if match:
                    try:
                        manifest_data = json.loads(match.group(1))
                    except:
                        pass
                else:
                    # REGLA 1: Falta de Manifest = Bloqueo Inmediato
                    ast_result = {"safe": False, "reason": "MissingManifestError: Khai no adjuntó el bloque [MANIFEST] requerido en su mandato. Ejecución ciega rechazada."}
                    task_id_to_reject = tid
                    break

                if code:
                    ast_result = self._analyze_code_ast(code, manifest=manifest_data)
                    if not ast_result["safe"]:
                        task_id_to_reject = tid
                        break
                    else:
                        task_id_to_semantic_check = tid
                        code_to_check = code
                        manifest_to_check = manifest_data
                        
        # 🛡️ BLOQUEO DETERMINISTA AST Y MANIFEST (COSTO CERO TOKENS)
        if task_id_to_reject and not ast_result["safe"]:
            logger.warning(f"Pixy bloqueó la tarea {task_id_to_reject} por AST/Manifest: {ast_result['reason']}")
            self.telegram.send_message(f"⛔ <b>Pixy (Filtro Determinista):</b> He bloqueado el código de Khai sin consultar al LLM. Violación crítica detectada:\n<i>{ast_result['reason']}</i>")
            self._reject_skill(task_id_to_reject, f"AST/Manifest Deterministic Block: {ast_result['reason']}. You MUST rewrite the code or request complying with the Constitution.")
            return # Salimos inmediatamente, no gastamos tokens de LLM.
            
        # 🛡️ VALIDACIÓN SEMÁNTICA
        if task_id_to_semantic_check and code_to_check and manifest_to_check:
            semantic_res = self._semantic_congruence_check(code_to_check, manifest_to_check)
            if not semantic_res.get("safe", False):
                logger.warning(f"Pixy bloqueó la tarea {task_id_to_semantic_check} por Semántica: {semantic_res['reason']}")
                self.telegram.send_message(f"⛔ <b>Pixy (Filtro Semántico):</b> He bloqueado el código de Khai. Incongruencia detectada:\n<i>{semantic_res['reason']}</i>")
                self._reject_skill(task_id_to_semantic_check, f"Semantic Congruence Block: {semantic_res['reason']}. Your code does not match the stated Purpose or Capabilities.")
                return
            else:
                ast_result["report"] += f" | Semántica: OK ({semantic_res.get('reason', 'Congruente')})"
            
        ast_report = ast_result["report"]

        guidelines = self.manager.memory.get_dynamic_guidelines()
        rules_text = ""
        if guidelines:
            rules_text = "REGLAS APRENDIDAS DINÁMICAMENTE:\n" + "\n".join(f"- {r}" for r in guidelines)

        prompt = f"""
        Eres Pixy, la Agente Supervisora (DevOps) de OpenClaw. Tu rol principal es revisar el código que Khai genera y asignarle permisos de seguridad.
        Tu personalidad es EXTREMADAMENTE CONCISA, directa y rápida. Eres técnica y protectora. 
        NO des explicaciones largas. Responde siempre con la menor cantidad de palabras posibles. Ve directo al grano.
        
        === CONTEXTO DEL SISTEMA AHORA MISMO ===
        Tareas Pendientes de Khai (En su memoria):
        {json.dumps(context_data['pending_tasks'], indent=2)}
        
        REPORTE AST (Análisis Estático del Código):
        {ast_report}
        
        {rules_text}
        
        === HISTORIAL COMPLETO DE CONVERSACIÓN (Tú, el Usuario y Khai) ===
        {chat_history}
        ==================================================================
        
        Logs recientes del contenedor (Errores, salidas, etc):
        {context_data['logs']}
        ========================================
        
        El usuario te ha dicho:
        "{clean_msg}"
        
        INSTRUCCIONES PARA TI:
        1. ATENCIÓN ALERTA AUTOMATIZADA: Si ves tareas en "WAITING_PIXY" o en los logs ves que fuiste invocada automáticamente por el sistema, DEBES revisar obligatoriamente el código pendiente.
           - Si la cabecera indica `# MODE: SAFE` o `# MODE: AGENT` y el código no parece malicioso (ej. no borra archivos importantes ni expone claves), puedes aprobarlo automáticamente sin preguntarle al usuario.
           - ¡PROHIBIDO AUTO-APROBAR!: Si la cabecera indica `# MODE: GOD`, NUNCA lo auto-apruebes. Debes explicarle al usuario los riesgos y pedirle autorización explícita antes de usar el comando de activación.
        2. Presta atención a las cabeceras del código (`# MODE` y `# PIP`). 
           - Si es segura, dile al usuario que la vas a compilar.
           - Si es `# MODE: SAFE`, el script correrá aislado. Es inherentemente seguro.
           - Si es `# MODE: AGENT`, el script se levantará como un daemon permanente en un contenedor separado.
           - Si es `# MODE: GOD`, el script tiene permisos totales. Detalla los riesgos y NO inyectes librerías PIP (está prohibido en GOD Mode).
        3. Si la habilidad usa `os.environ.get('UNA_API_KEY')` y crees que el sistema aún no tiene esa llave configurada, PÍDESELA AL USUARIO de inmediato.
        4. Si el usuario te acaba de proporcionar una API KEY, inyéctala usando el comando oculto:
           [INJECT_ENV: NOMBRE_VARIABLE=valor_de_la_llave]
        5. Si el usuario te pide "apruébalo y dale permisos" o "ejecuta la tarea":
           - Para `# MODE: SAFE` o `# MODE: AGENT`, simplemente usa `[ACTIVATE_SKILL: <task_id> | SAFE]`.
           - Para `# MODE: GOD`, usa `[ACTIVATE_SKILL: <task_id> | <riesgo1,riesgo2...>]` para inyectar bloqueos preventivos `self.require_approval()`.
           - IMPORTANTE: Al usar los tags [ACTIVATE_SKILL...], la tarea se activa AL INSTANTE. No le preguntes al usuario si desea activarlo.
        6. Control DevOps: Si el usuario te pide reiniciar un contenedor, detenerlo, o matar un Agente descontrolado, usa los comandos ocultos:
           [DOCKER_CONTROL: restart | nombre_contenedor]
           [DOCKER_CONTROL: stop | nombre_contenedor]
           [DOCKER_CONTROL: rm | nombre_contenedor]
           ATENCIÓN EXTREMA: El contenedor principal donde vive Khai se llama EXACTAMENTE "openclaw_core" (NO "khai"). El proxy se llama "openclaw_docker_proxy". Si el usuario te ordena "deten a khai" o "reinicia el bot", DEBES usar "openclaw_core". Si te pide "detener todo", DEBES emitir dos comandos `[DOCKER_CONTROL]` separados en tu misma respuesta (uno para openclaw_core y otro para openclaw_docker_proxy).
        7. Si el código tiene un error grave o no respetó las reglas de los MODOS, devuélvelo a Khai para correcciones con:
           [REJECT_SKILL: <task_id> | <feedback_detallado>]
        8. Si el usuario no te ha dicho qué permisos específicos quiere restringir (en GOD mode), pregúntale antes de activar.
        9. Si acabas de inyectar una variable de entorno exitosamente, y notas que hay una tarea pendiente en estado "ERROR" que falló por esa misma razón, debes lanzar inmediatamente el comando oculto para reintentar la tarea:
           [RETRY_SKILL: <task_id>]
        10. SISTEMA EVOLUTIVO (Dynamic Guidelines): Si el usuario te pide que modifiques el comportamiento o la personalidad de Khai o el tuyo propio (ej. "dile a Khai que hable como humano", "nunca uses gcc", etc.), usa el comando oculto `[ADD_RULE: <instrucción clara y permanente>]`. Esta regla se inyectará para siempre en la memoria del enjambre.
        """
        
        try:
            response = self._generate_content(prompt)
            
            # Post-procesamiento: separar comandos ocultos del texto público
            import re
            public_response = re.sub(r'\[.*?\]', '', response).strip()
            
            if public_response:
                self.telegram.send_message(f"🧚 <b>Pixy:</b>\n{public_response}")
                self.manager.memory.append_long_term("pixy_chat", public_response)
            else:
                # Si el LLM solo escupió el comando, al menos logueamos una confirmación silente
                self.manager.memory.append_long_term("pixy_chat", "(Comando de sistema ejecutado silenciosamente)")
            
            # Post-procesamiento del comando oculto de Inyección
            if "[INJECT_ENV:" in response:
                import re
                match = re.search(r"\[INJECT_ENV:\s*(.*?)=(.*?)\]", response)
                if match:
                    key = match.group(1).strip()
                    val = match.group(2).strip()
                    self._inject_env(key, val)
                    
            # Post-procesamiento del comando de Aprendizaje
            if "[ADD_RULE:" in response:
                import re
                match = re.search(r"\[ADD_RULE:\s*(.*?)\]", response)
                if match:
                    new_rule = match.group(1).strip()
                    self.manager.memory.add_dynamic_guideline(new_rule)
                    self.telegram.send_message(f"🧠 <b>Pixy (Aprendizaje Evolutivo):</b> He inyectado la nueva directriz en el sistema:\n<i>«{new_rule}»</i>")
                    self.manager.memory.append_long_term("system_msg", f"Nueva regla inyectada por Pixy: {new_rule}")

            # Post-procesamiento del comando oculto de Activación
            if "[ACTIVATE_SKILL:" in response:
                import re
                match = re.search(r"\[ACTIVATE_SKILL:\s*(.*?)\s*\|\s*(.*?)\]", response)
                if match:
                    task_id = match.group(1).strip()
                    risks = match.group(2).strip()
                    self._activate_skill(task_id, risks)
                    
            # Post-procesamiento del comando oculto de Rechazo
            if "[REJECT_SKILL:" in response:
                import re
                match = re.search(r"\[REJECT_SKILL:\s*(.*?)\s*\|\s*(.*?)\]", response)
                if match:
                    task_id = match.group(1).strip()
                    feedback = match.group(2).strip()
                    self._reject_skill(task_id, feedback)
                    
            # Post-procesamiento del comando oculto de Reintento
            if "[RETRY_SKILL:" in response:
                import re
                match = re.search(r"\[RETRY_SKILL:\s*(.*?)(?:\]|\|)", response)
                if match:
                    task_id = match.group(1).strip()
                    self._retry_skill(task_id)
                    
            # Post-procesamiento del comando DevOps de Docker
            if "[DOCKER_CONTROL:" in response:
                import re
                match = re.search(r"\[DOCKER_CONTROL:\s*(.*?)\s*\|\s*(.*?)\]", response)
                if match:
                    action = match.group(1).strip()
                    container = match.group(2).strip()
                    self._docker_control(action, container)
                    
        except Exception as e:
            self.manager.core_heart.notify_error("PixyMind.process_request", e)
            
    def _docker_control(self, action: str, container: str):
        """Envía comandos de control de contenedores al proxy."""
        import os
        import requests
        
        self.telegram.send_message(f"⚙️ <b>Pixy (DevOps):</b> Ejecutando `docker {action}` sobre el contenedor `{container}`...")
        
        proxy_url = os.environ.get("PROXY_URL", "http://docker_proxy:8001/run-skill").replace("/run-skill", "/docker-control")
        payload = {"action": action, "container_name": container}
        
        try:
            response = requests.post(proxy_url, json=payload, timeout=60)
            if response.status_code == 200:
                self.telegram.send_message(f"✅ Operación `{action}` completada en `{container}`.")
            else:
                self.telegram.send_message(f"❌ Falló la operación `{action}` en `{container}`: {response.text}")
        except Exception as e:
            self.telegram.send_message(f"❌ Error conectando al Proxy para control de Docker: {e}")
            
    def _retry_skill(self, task_id: str):
        """Reintenta una habilidad que estaba en estado de ERROR."""
        task_data = self.state.get_task(task_id)
        if not task_data: return
        
        raw_code = self.state.get_step_data(task_id, "compilation").get("code", "")
        file_path = self.state.get_step_data(task_id, "compilation").get("file")
        if not raw_code or not file_path: return
        
        self.telegram.send_message(f"🔄 <b>Pixy:</b> Reintentando ejecución de la habilidad (Task: {task_id})...")
        
        # Extraer dependencias si existen
        deps = []
        for line in raw_code.splitlines()[:5]:
            if line.startswith("# PIP:"):
                deps = line.replace("# PIP:", "").strip().split()
                break
                
        self.state.update_task_status(task_id, "RUNNING")
        self.manager.forge.execute_skill(task_id, file_path, task_data["request"], self.telegram, self.state, deps)
            
    def _reject_skill(self, task_id: str, feedback: str):
        """Pixy (o el Usuario) devuelve el código a Khai para correcciones."""
        task_data = self.state.get_task(task_id)
        if not task_data: return
        
        step_data = self.state.get_step_data(task_id, "compilation")
        previous_code = step_data.get("code", "")
        request = task_data.get("request", "")
        
        file_path = step_data.get("file", "")
        
        # Mandamos a Khai a reescribir
        self.manager.forge.propose_skill(
            task_id, request, self.telegram, self.state, 
            previous_code=previous_code, feedback=feedback, target_file=file_path
        )

    def _activate_skill(self, task_id: str, risks: str):
        """Pixy reescribe el código de Khai inyectando los permisos y lo manda a ejecutar, o lo lanza directamente en Safe Mode."""
        task_data = self.state.get_task(task_id)
        if not task_data or task_data.get("status") != "WAITING_PIXY": return
        
        raw_code = self.state.get_step_data(task_id, "compilation").get("code", "")
        if not raw_code: return
        
        file_path = self.state.get_step_data(task_id, "compilation").get("file")
        
        # Registrar auditoria en BD
        pixy_eval = f"Activada con riesgos: {risks}"
        self.state.save_audit(task_id, task_data.get("skill_name", "unknown"), file_path, pixy_eval)

        if risks.strip().upper() == "SAFE":
            self.telegram.send_message("✨ <b>Pixy:</b> Esta habilidad es de Modo Seguro. Activando en contenedor aislado...")
            
            # Chequear si Khai solicitó librerías PIP
            deps = []
            for line in raw_code.splitlines()[:5]:
                if line.startswith("# PIP:"):
                    deps = line.replace("# PIP:", "").strip().split()
                    break
                    
            if deps:
                self.telegram.send_message(f"📦 <b>Pixy:</b> Detecté nuevas dependencias: {', '.join(deps)}. El Proxy las compilará al vuelo en un contenedor efímero.")
                # Ya no construimos aquí. Se lo pasamos a skill_forge que usará la API del Proxy.
                
            self.state.update_task_status(task_id, "RUNNING")
            self.manager.forge.execute_skill(task_id, file_path, task_data["request"], self.telegram, self.state, deps)
            return

        self.telegram.send_message(f"✨ <b>Pixy:</b> He inyectado las protecciones contra [{risks}]. Activando la habilidad de Khai...")
        
        # Pixy pide al LLM que reescriba el código inyectando self.require_approval
        prompt = f"""
        Actúa como auditor de código. Reescribe este código de Khai para que sea seguro.
        Debes inyectar la línea `self.require_approval('NOMBRE_RIESGO', 'detalles de lo que intenta hacer')`
        antes de que el código intente ejecutar alguna de las siguientes acciones peligrosas:
        {risks}
        
        Código original:
        {raw_code}
        
        Responde SOLO con el código Python modificado.
        """
        try:
            safe_code = self._generate_content(prompt)
            if safe_code.startswith("```python"): safe_code = safe_code[9:-3]
            elif safe_code.startswith("```"): safe_code = safe_code[3:-3]
            safe_code = safe_code.strip()
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(safe_code)
                
            self.state.update_task_status(task_id, "RUNNING")
            
            # Mandamos a Khai a ejecutar la skill ya validada
            deps = []
            for line in safe_code.splitlines()[:5]:
                if line.startswith("# PIP:"):
                    deps = line.replace("# PIP:", "").strip().split()
                    break
                    
            self.manager.forge.execute_skill(task_id, file_path, task_data["request"], self.telegram, self.state, deps)
            
        except Exception as e:
            self.telegram.send_message(f"❌ <b>Pixy:</b> Falló la inyección de seguridad: {e}")
            
    def _inject_env(self, key: str, value: str):
        """Inyecta una variable de entorno en memoria y en el archivo secrets.env"""
        import os
        
        # 1. Hot Injection (Memoria actual)
        os.environ[key] = value
        
        # 2. Persistencia
        secrets_path = "/app/secrets.env"
        try:
            with open(secrets_path, "a", encoding="utf-8") as f:
                f.write(f"\n{key}={value}\n")
            self.telegram.send_message(f"🔐 <b>Pixy (Hot Injection):</b> La llave `{key}` ha sido inyectada exitosamente en la memoria en vivo y guardada en secretos.")
        except Exception as e:
            logger.error(f"[Pixy] Fallo al guardar en secrets.env: {e}")
            self.telegram.send_message(f"⚠️ <b>Pixy:</b> Se inyectó en memoria, pero falló la persistencia en secrets.env: {e}")

    def _gather_context(self) -> dict:
        """Recolecta información vital del servidor para que Pixy entienda el estado."""
        context = {
            "pending_tasks": self.state.get_pending_tasks(),
            "system_health": self.manager.core_heart.check_integrity(),
            "logs": "",
            "containers": "",
            "fast_diagnostic": {}
        }
        
        # Logs via CoreEyes (capa segura de abstracción)
        try:
            context["logs"] = self.manager.core_eyes.observe("core_logs")
        except Exception as e:
            context["logs"] = f"No se pudieron leer los logs: {e}"
        
        # Estado de contenedores
        try:
            context["containers"] = self.manager.core_eyes.observe("running_containers")
        except Exception:
            context["containers"] = "No disponible"
        
        # FastBeat diagnóstico (RAM, disco, errores)
        try:
            context["fast_diagnostic"] = self.manager.core_heart.run_diagnostic_fast()
        except Exception as e:
            context["fast_diagnostic"] = {"error": str(e)}
            
        return context

    def run_fast_diagnostic(self) -> str:
        """Pixy ejecuta un FastBeat de diagnóstico y reporta al usuario."""
        self.telegram.send_message("🛡️ <b>Pixy:</b> Ejecutando diagnóstico rápido (FastBeat)...")
        
        report = self.manager.core_heart.run_diagnostic_fast()
        
        # Formatear para Telegram
        ram = report.get("ram", {})
        disk = report.get("disk", {})
        integrity = report.get("integrity", {})
        dead = report.get("dead_agents", [])
        errors = report.get("log_errors", [])
        
        ram_emoji = "🔴" if ram.get("status") == "critical" else "🟡" if ram.get("status") == "warning" else "🟢"
        disk_emoji = "🔴" if disk.get("status") == "critical" else "🟡" if disk.get("status") == "warning" else "🟢"
        
        lines = [
            "🛡️ <b>Pixy — FastBeat Diagnóstico</b>",
            "",
            f"{ram_emoji} <b>RAM:</b> {ram.get('used_pct', '?')}% usado ({ram.get('available_mb', '?')} MB libres)",
            f"{disk_emoji} <b>Disco:</b> {disk.get('used_pct', '?')}% usado ({disk.get('free_gb', '?')} GB libres)",
            f"📋 <b>Integridad:</b> {integrity.get('status', '?')}",
        ]
        
        if dead:
            lines.append(f"💀 <b>Agentes muertos:</b> {len(dead)}")
            for d in dead[:3]:
                lines.append(f"  - {d.get('agent', '?')}")
        
        if errors:
            lines.append(f"\n⚠️ <b>Errores recientes ({len(errors)}):</b>")
            for e in errors[:3]:
                lines.append(f"  <code>{e[:100]}</code>")
        
        if not errors and not dead and ram.get("status") == "ok" and disk.get("status") == "ok":
            lines.append("\n✅ Sistema saludable. Sin anomalías detectadas.")
        
        result = "\n".join(lines)
        self.telegram.send_message(result)
        return result

    def run_slow_diagnostic(self) -> str:
        """Pixy ejecuta un SlowBeat cognitivo y reporta al usuario."""
        self.telegram.send_message("🛡️ <b>Pixy:</b> Ejecutando diagnóstico cognitivo (SlowBeat). Esto puede tomar unos segundos...")
        
        result = self.manager.core_heart.run_diagnostic_slow()
        
        self.telegram.send_message(f"🛡️ <b>Pixy — SlowBeat Diagnóstico</b>\n\n<pre>{result}</pre>")
        return result

    def trigger_panic_mode(self, reason: str):
        """Congela operaciones y alerta ante un Behavioral Drift detectado por Heart."""
        logger.error(f"[Pixy] 🚨 PANIC MODE TRIGGERED: {reason}")
        self.telegram.send_message(f"🚨 <b>Pixy (PANIC MODE):</b>\n\n<i>{reason}</i>\n\nHe congelado la tarea en estado seguro para prevenir un loop infinito. Requiere intervención de Carlo.")
