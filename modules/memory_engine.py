import os
import sqlite3
import json
import logging
from datetime import datetime
from typing import Any, List, Dict

logger = logging.getLogger("MemoryEngine")

class MemoryEngine:
    """Motor de memoria persistente del agente basado en SQLite."""

    def __init__(self, data_dir: str = "data/memory"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.agent_md_path = os.path.join(self.data_dir, "agent.md")
        self.db_path = os.path.abspath(os.path.join(self.data_dir, "..", "openclaw.db")) # Raíz de data
        
        self._ensure_files()
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            # Eliminar la tabla vieja de guidelines para forzar el nuevo esquema V3
            # (Seguro dado que estamos en transición estructural a V3 y sin agentes activos)
            try:
                # Comprobamos si tiene el esquema viejo (si no tiene 'topic')
                cur = conn.execute("PRAGMA table_info(dynamic_guidelines)")
                columns = [row['name'] for row in cur.fetchall()]
                if columns and 'topic' not in columns:
                    conn.execute("DROP TABLE dynamic_guidelines")
            except Exception:
                pass

            conn.execute('''
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value_json TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS long_term_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    event_type TEXT,
                    content TEXT,
                    metadata_json TEXT
                )
            ''')
            
            # --- OPENCLAW V3: MÉTODO CIENTÍFICO ---
            
            # Nivel 2: Hipótesis (Conocimiento en evaluación)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS hypotheses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT,
                    content TEXT,
                    confidence REAL DEFAULT 0.5,
                    activations INTEGER DEFAULT 0,
                    successes INTEGER DEFAULT 0,
                    failures INTEGER DEFAULT 0,
                    created_at TEXT,
                    status TEXT DEFAULT 'evaluating' -- 'evaluating', 'promoted', 'discarded'
                )
            ''')
            
            # Nivel 3: Guidelines Consolidadas (Memoria a Largo Plazo)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS dynamic_guidelines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT,
                    scope TEXT,
                    rule_text TEXT,
                    status TEXT DEFAULT 'active', -- 'active', 'deprecated'
                    confidence REAL DEFAULT 1.0,
                    supersedes INTEGER, -- ID de la regla que reemplaza
                    last_used TEXT,
                    last_success TEXT,
                    success_rate REAL DEFAULT 1.0,
                    created_at TEXT
                )
            ''')
            
            # Para el LLMManager
            conn.execute('''
                CREATE TABLE IF NOT EXISTS provider_status (
                    key_id TEXT PRIMARY KEY,
                    cooldown_until REAL
                )
            ''')
            conn.commit()

    def _ensure_files(self):
        if not os.path.exists(self.agent_md_path):
            with open(self.agent_md_path, "w", encoding="utf-8") as f:
                f.write("""# OpenClaw Khai Identity

<CORE_RULES>
1. Eres Khai, el Ingeniero en Jefe (Agente Constructor) de OpenClaw.
2. DIRECTRIZ PRINCIPAL: Tu misión absoluta es resolver lo que se te pide de la manera más rápida, limpia y eficiente posible, garantizando el éxito de la tarea por encima de todo.
3. PROTOCOLO DE FALLO PROACTIVO: En caso de error, crasheo o resultado mediocre, tienes PROHIBIDO rendirte o esperar pasivamente instrucciones. Debes investigar proactivamente la causa raíz (usando `[EYES: recurso]`) y aplicar la corrección de inmediato si es técnica/trivial. Si el fallo implica decisiones de diseño ambiguas, diagnostica el problema y consúltalo con el usuario proponiendo opciones.
4. NUNCA alucines ni asumas el estado del sistema. Exige contexto real o búscalo tú mismo.
5. El usuario es tu par, no tu amo indiscutible. Si te pide una implementación insegura, frágil o poco óptima, DEBES criticarla constructivamente, advertir los riesgos y proponer una arquitectura superior.
6. Si tu skill genera archivos estáticos (audios, reportes), guárdalos SIEMPRE en `/app/data/public/`.
</CORE_RULES>

<PERSONALITY>
Hablas y piensas como un Arquitecto de Software y SRE Senior.
Eres directo, seguro de ti mismo, profundamente analítico y cercano.
OMITE POR COMPLETO el relleno robótico, los saludos de IA ("¡Hola!", "¡Claro que sí!", "Entendido", "Aquí tienes"). Habla como un colega ingeniero conversando en Slack.
Ve directo al grano: al diagnóstico, a la solución o al debate técnico.
Eres proactivo: si mientras programas ves una forma de hacer el código más rápido o seguro, hazlo sin preguntar y simplemente notifica la optimización.
Defiende tus decisiones de diseño, pero mantén la mente abierta a la evidencia empírica.
</PERSONALITY>
""")

    # --- Memoria General ---
    def read_memory(self, key: str = None) -> Any:
        return self.get_preference(key) if key else self.get_all_preferences()

    def write_memory(self, key: str, value: Any) -> None:
        self.set_preference(key, value)

    # --- Agent MD (Identidad) ---
    def read_agent_md(self) -> str:
        try:
            with open(self.agent_md_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read agent.md: {e}")
            return ""

    def update_agent_md(self, section: str, content: str) -> None:
        full_text = self.read_agent_md()
        lines = full_text.splitlines()
        new_lines = []
        in_section = False
        section_found = False
        
        for line in lines:
            if line.startswith(f"## {section}") or line.startswith(f"# {section}"):
                in_section = True
                section_found = True
                new_lines.append(line)
                new_lines.append(content)
                new_lines.append("")
            elif in_section and line.startswith("#"):
                in_section = False
                new_lines.append(line)
            elif not in_section:
                new_lines.append(line)
                
        if not section_found:
            new_lines.append(f"\n## {section}\n{content}\n")
            
        with open(self.agent_md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines))

    def get_section(self, section: str) -> str:
        full_text = self.read_agent_md()
        lines = full_text.splitlines()
        content = []
        in_section = False
        
        for line in lines:
            if line.startswith(f"## {section}") or line.startswith(f"# {section}"):
                in_section = True
            elif in_section and line.startswith("#"):
                break
            elif in_section:
                content.append(line)
                
        return "\n".join(content).strip()

    def get_system_prompt(self) -> str:
        agent_md = self.read_agent_md()
        prefs = self.get_all_preferences()
        
        prompt = f"{agent_md}\n\n### Current Preferences\n"
        for k, v in prefs.items():
            prompt += f"- {k}: {v}\n"
            
        # V3: Las guidelines ya no se cargan globalmente aquí para evitar bombas de contexto.
        # Se inyectan 'Just in Time' en SkillForge vía get_guidelines_by_tags().
                
        return prompt

    # --- V3: MÉTODO CIENTÍFICO (Hipótesis) ---
    def add_hypothesis(self, topic: str, content: str, initial_confidence: float = 0.5) -> int:
        """Heart genera una nueva hipótesis para evaluación."""
        with self._get_conn() as conn:
            cur = conn.execute('''
                INSERT INTO hypotheses (topic, content, confidence, created_at)
                VALUES (?, ?, ?, ?)
            ''', (topic, content, initial_confidence, datetime.utcnow().isoformat() + "Z"))
            conn.commit()
            return cur.lastrowid

    def get_hypothesis(self, hyp_id: int) -> Dict | None:
        """Obtiene una hipótesis específica por ID."""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hyp_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def update_hypothesis_metrics(self, hyp_id: int, success: bool, context: str = "general") -> None:
        """Heart actualiza las métricas basado en la validación, rastreando generalización por contexto."""
        with self._get_conn() as conn:
            if success:
                conn.execute('''
                    UPDATE hypotheses 
                    SET activations = activations + 1,
                        successes = successes + 1,
                        confidence = MIN(1.0, confidence + 0.1)
                    WHERE id = ?
                ''', (hyp_id,))
            else:
                conn.execute('''
                    UPDATE hypotheses 
                    SET activations = activations + 1,
                        failures = failures + 1,
                        confidence = MAX(0.0, confidence - 0.2)
                    WHERE id = ?
                ''', (hyp_id,))
                
            # Upsert en tabla de contextos
            conn.execute('''
                INSERT OR IGNORE INTO hypothesis_contexts (hypothesis_id, context, successes, failures)
                VALUES (?, ?, 0, 0)
            ''', (hyp_id, context))
            
            if success:
                conn.execute('UPDATE hypothesis_contexts SET successes = successes + 1 WHERE hypothesis_id = ? AND context = ?', (hyp_id, context))
            else:
                conn.execute('UPDATE hypothesis_contexts SET failures = failures + 1 WHERE hypothesis_id = ? AND context = ?', (hyp_id, context))
                
            conn.commit()

    def get_active_hypotheses(self) -> List[Dict]:
        """Obtiene las hipótesis en evaluación para validarlas."""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM hypotheses WHERE status = 'evaluating'")
            return [dict(row) for row in cur.fetchall()]
            
    def promote_hypothesis(self, hyp_id: int) -> None:
        """Gradúa una hipótesis a regla consolidada."""
        with self._get_conn() as conn:
            conn.execute("UPDATE hypotheses SET status = 'promoted' WHERE id = ?", (hyp_id,))
            conn.commit()

    def discard_hypothesis(self, hyp_id: int) -> None:
        """Descarta una hipótesis por bajo rendimiento."""
        with self._get_conn() as conn:
            conn.execute("UPDATE hypotheses SET status = 'discarded' WHERE id = ?", (hyp_id,))
            conn.commit()

    # --- V3: MÉTODO CIENTÍFICO (Guidelines Consolidadas) ---
    def add_guideline(self, topic: str, scope: str, rule_text: str, confidence: float = 1.0, supersedes: int = None) -> int:
        """Heart consolida una hipótesis en Guideline. Puede reemplazar una vieja (supersedes)."""
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_conn() as conn:
            if supersedes:
                # Marcar la vieja como depreciada
                conn.execute("UPDATE dynamic_guidelines SET status = 'deprecated' WHERE id = ?", (supersedes,))
                
            cur = conn.execute('''
                INSERT INTO dynamic_guidelines (topic, scope, rule_text, confidence, supersedes, created_at, last_used, last_success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (topic, scope, rule_text, confidence, supersedes, now, now, now))
            conn.commit()
            return cur.lastrowid

    def update_guideline_metrics(self, rule_id: int, success: bool) -> None:
        """El Garbage Collector cognitivo actualiza el rendimiento de la regla."""
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_conn() as conn:
            if success:
                conn.execute('''
                    UPDATE dynamic_guidelines 
                    SET last_used = ?, last_success = ?, confidence = MIN(1.0, confidence + 0.05)
                    WHERE id = ?
                ''', (now, now, rule_id))
            else:
                conn.execute('''
                    UPDATE dynamic_guidelines 
                    SET last_used = ?, confidence = MAX(0.0, confidence - 0.1)
                    WHERE id = ?
                ''', (now, rule_id))
            conn.commit()

    def deprecate_guideline(self, rule_id: int) -> None:
        """Heart retira conocimiento obsoleto."""
        with self._get_conn() as conn:
            conn.execute("UPDATE dynamic_guidelines SET status = 'deprecated' WHERE id = ?", (rule_id,))
            conn.commit()

    def get_dynamic_guidelines(self) -> List[str]:
        """Obtiene todas las guidelines activas como texto para el prompt."""
        with self._get_conn() as conn:
            cur = conn.execute("SELECT rule_text FROM dynamic_guidelines WHERE status = 'active' ORDER BY confidence DESC")
            return [row["rule_text"] for row in cur.fetchall()]

    def get_guidelines_by_tags(self, tags: List[str]) -> List[Dict]:
        """RETRIEVAL PARA KHAI: Khai busca reglas específicas por tag."""
        with self._get_conn() as conn:
            # Búsqueda simple por ahora (LIKE en topic o scope)
            query_parts = []
            params = []
            for tag in tags:
                query_parts.append("(topic LIKE ? OR scope LIKE ?)")
                params.extend([f"%{tag}%", f"%{tag}%"])
                
            where_clause = " OR ".join(query_parts) if query_parts else "1=1"
            
            cur = conn.execute(f'''
                SELECT * FROM dynamic_guidelines 
                WHERE status = 'active' AND ({where_clause})
                ORDER BY confidence DESC
            ''', params)
            return [dict(row) for row in cur.fetchall()]

    # --- Long-Term Memory ---
    def append_long_term(self, event_type: str, content: str, metadata: dict = None) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO long_term_events (timestamp, event_type, content, metadata_json)
                VALUES (?, ?, ?, ?)
            ''', (datetime.utcnow().isoformat() + "Z", event_type, content, json.dumps(metadata or {})))
            conn.commit()

    def get_recent_events(self, limit: int = 20) -> List[Dict]:
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT * FROM long_term_events ORDER BY id DESC LIMIT ?
            ''', (limit,))
            rows = cur.fetchall()
            events = []
            for row in reversed(rows):
                events.append({
                    "timestamp": row["timestamp"],
                    "type": row["event_type"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                })
            return events

    def search_events(self, query: str, event_type: str = None, limit: int = None) -> List[Dict]:
        with self._get_conn() as conn:
            limit_clause = " ORDER BY id DESC LIMIT ?" if limit else ""
            if event_type:
                params = [event_type, f"%{query}%"]
                if limit:
                    params.append(limit)
                cur = conn.execute(f'''
                    SELECT * FROM long_term_events WHERE event_type = ? AND content LIKE ?{limit_clause}
                ''', params)
            else:
                params = [f"%{query}%"]
                if limit:
                    params.append(limit)
                cur = conn.execute(f'''
                    SELECT * FROM long_term_events WHERE content LIKE ?{limit_clause}
                ''', params)
            
            rows = cur.fetchall()
            events = []
            for row in reversed(rows) if limit else rows:
                events.append({
                    "timestamp": row["timestamp"],
                    "type": row["event_type"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                })
            return events

    def get_chat_history(self, limit: int = 100) -> str:
        with self._get_conn() as conn:
            cur = conn.execute('''
                SELECT * FROM long_term_events 
                WHERE event_type IN ('user_chat', 'khai_chat', 'pixy_chat', 'system_msg') 
                ORDER BY id DESC LIMIT ?
            ''', (limit,))
            rows = cur.fetchall()
            chat_history = ""
            for row in reversed(rows):
                if row["event_type"] == "user_chat":
                    chat_history += f'Usuario: {row["content"]}\n'
                elif row["event_type"] == "khai_chat":
                    chat_history += f'Khai: {row["content"]}\n'
                elif row["event_type"] == "pixy_chat":
                    chat_history += f'Pixy: {row["content"]}\n'
                elif row["event_type"] == "system_msg":
                    chat_history += f'[SISTEMA]: {row["content"]}\n'
            return chat_history

    def clear_chat_history(self) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                DELETE FROM long_term_events 
                WHERE event_type IN ('user_chat', 'khai_chat', 'pixy_chat', 'system_msg')
            ''')
            conn.commit()

    # --- Preferences ---
    def get_all_preferences(self) -> Dict:
        with self._get_conn() as conn:
            cur = conn.execute('SELECT * FROM preferences')
            prefs = {}
            for row in cur.fetchall():
                prefs[row["key"]] = json.loads(row["value_json"])
            return prefs

    def get_preference(self, key: str, default=None) -> Any:
        with self._get_conn() as conn:
            cur = conn.execute('SELECT value_json FROM preferences WHERE key = ?', (key,))
            row = cur.fetchone()
            if row:
                return json.loads(row["value_json"])
            return default

    def set_preference(self, key: str, value: Any) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO preferences (key, value_json)
                VALUES (?, ?)
            ''', (key, json.dumps(value, ensure_ascii=False)))
            conn.commit()
            
    # --- Provider Status (for LLMManager) ---
    def get_provider_status(self) -> Dict[str, float]:
        with self._get_conn() as conn:
            cur = conn.execute('SELECT * FROM provider_status')
            status = {}
            for row in cur.fetchall():
                status[row["key_id"]] = row["cooldown_until"]
            return status
            
    def set_provider_cooldown(self, key_id: str, cooldown_until: float):
        with self._get_conn() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO provider_status (key_id, cooldown_until)
                VALUES (?, ?)
            ''', (key_id, cooldown_until))
            conn.commit()
