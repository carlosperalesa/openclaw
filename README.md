# OpenClaw V3: Framework Agéntico Semi-Autónomo

## 📋 Descripción

OpenClaw V3 es un sistema operativo multi-agente impulsado por IA, diseñado no solo para programar y ejecutar Habilidades (Skills) al vuelo, sino para **aprender, consolidar, adaptarse y protegerse** sin depender de corrección manual constante.

A diferencia de los asistentes estáticos o de la versión V2 (reactiva), OpenClaw V3 aplica el **Método Científico** sobre su propia existencia: genera hipótesis a partir de sus fallos, las somete a prueba empírica, las consolida como *Guidelines* si funcionan, y cuenta con un *Garbage Collector Cognitivo* que destruye reglas inútiles o contradictorias para evitar la degradación del conocimiento a largo plazo.

- **Stack**: FastAPI + SQLite + Gemini API / Azure OpenAI + Docker (Dinámico + Proxy)
- **Hosting**: Linux VPS (Ubuntu)
- **Comunicación**: Telegram Webhooks

---

## 🧠 Filosofía V3: El Método Científico

La arquitectura V3 resuelve el mayor desafío de los sistemas autónomos a largo plazo: **La degradación del conocimiento**.

1. **Hipótesis en lugar de Leyes Ciegas**: Heart nunca genera reglas estáticas basadas en un solo error. En su lugar, registra múltiples *Hipótesis* con niveles de confianza y las somete a prueba empírica.
2. **Fallback Cognitivo**: Si la hipótesis activa falla y existen alternativas, SkillForge promueve la siguiente hipótesis y relanza la tarea con el mismo `task_id`, sin pedirle a CoreMind un análisis desde cero.
3. **Contexto Empírico**: Las hipótesis acumulan éxitos y fracasos por contexto en `hypothesis_contexts`, permitiendo distinguir dónde una estrategia funciona y dónde se rompe.
4. **Ciclo de Consolidación**: Si una hipótesis eleva la tasa de éxito de las Skills creadas por Khai, se promueve a *Dynamic Guideline*.
5. **Garbage Collection Cognitivo**: Si una regla o hipótesis reduce la tasa de éxito, el sistema la marca automáticamente como `deprecated` (olvido algorítmico).
6. **Resolución de Contradicciones**: El sistema reconoce cuando el entorno muta (ej. un paquete de Linux cambia de nombre) y superpone reglas nuevas sobre las antiguas mediante el rastreo de `supersedes`.

---

## 👥 Ecosistema de Agentes y Componentes V3

| Entidad | Módulo | Función en V3 |
|---------|------------|---------|
| **Khai** | `CoreMind` / `SkillForge` | **El Ingeniero en Jefe**. Su directriz absoluta es el éxito de la tarea. Ante un error, genera causas alternativas con confianza explícita, utiliza sus "ojos" para diagnóstico autónomo y corrige al vuelo. Si una hipótesis falla, promueve otra sin reiniciar el razonamiento causal. |
| **Pixy** | `PixyMind` | **DevOps y La Constitución**. Ahora posee un motor determinista (AST). Antes de consultar al LLM, analiza estáticamente el código de Khai contra el `constitution.yaml`. Si Khai rompe una regla inmutable (ej. `os.system`), Pixy destruye el código a costo de **0 Tokens**. |
| **Heart** | `CoreHeart` | **El Consolidador**. Posee dos motores: **FastBeat** (monitoreo vital barato, detección de loops, drift de confianza y abuso de Eyes) y **SlowBeat** (motor cognitivo asíncrono que procesa el cementerio de contenedores para formular hipótesis JSON). |
| **Eyes** | `CoreEyes` | **Observabilidad Segura**. Una capa de abstracción absoluta. Khai no puede ejecutar comandos shell de lectura. Solo puede pedir recursos a través de IDs autorizados (`core_logs`, `agent_logs_X`), siempre indicando una razón de investigación y blindando al host contra ataques de *Path Traversal*. |

---

## 🧪 Capa Causal y Antibucle

La Fase 3 añade un pipeline causal explícito para que OpenClaw no caiga en el sesgo de "la primera explicación gana":

- `CoreMind` separa intents normales de análisis de fallo y exige `possible_causes`, `confidence`, `proposed_fix`, `evidence` e `investigate_reason` cuando corresponde investigar.
- `CoreHeart.register_multiple_hypotheses()` guarda la hipótesis principal y sus alternativas; `StateManager` conserva la lista en `alternative_hypotheses`.
- `SkillForge` actualiza métricas por contexto y, ante error definitivo, llama a `promote_next_hypothesis()` para relanzar automáticamente con la siguiente causa probable.
- `FastBeat` distingue un síntoma sano de aprendizaje (misma hipótesis perdiendo confianza) de un loop real sin progreso.
- Cada lectura de `CoreEyes` guarda un hash del output. Si Khai lee tres veces el mismo recurso con el mismo hash sin generar código entre medio, Pixy activa Panic Mode para cortar la fábrica de tokens.

---

## 🔄 Arquitectura de Ejecución (Modes)

El orquestador principal (`openclaw_core`) **nunca ejecuta código dinámico nativamente**. Todo código viaja a través del Docker Proxy hacia entornos herméticos.

1. **SAFE MODE**: Para tareas analíticas o lógicas cortas. El script corre en un sandbox efímero que se destruye al terminar.
2. **AGENT MODE**: Para servidores, bots y procesos largos. El proxy levanta un contenedor Daemon (`docker run -d`) independiente que sobrevive a reinicios del Core.
3. **GOD MODE**: Para administración del sistema host. Se somete a la auditoría estricta AST de Pixy y requiere aprobación manual por el usuario.

---

## 📁 Estructura del Proyecto

```text
DOopenclawYT/
├── config/                            # Archivos de Configuración del Ecosistema
│   ├── config.yaml                    # Configuración estática general
│   └── constitution.yaml              # Leyes Inmutables de Seguridad evaluadas vía AST por Pixy
├── modules/                           # Componentes Core
│   ├── core_mind.py                   # Orquestador Semántico (Prompt System V3 de Khai)
│   ├── skill_forge.py                 # Creador de Skills, Auto-corrección e inyección PIP
│   ├── core_heart.py                  # Consolidador Cognitivo (FastBeat & SlowBeat)
│   ├── memory_engine.py               # SQLite V3 (Tabla de Hipótesis y Guidelines con GC)
│   ├── pixy.py                        # Evaluador Determinista AST y DevOps LLM
│   └── core_eyes.py                   # Lector Abstraído de Recursos (Lista Blanca)
├── docker_proxy/                      # API aislada con acceso al docker.sock del Host
├── data/                              # Volumen persistente (SQLite, identidades vivas y cementerio)
│   ├── memory/agent.md                # Personalidad mutable de Khai
│   └── public/                        # Único directorio expuesto de salida de archivos
├── orchestrator.py                    # Servidor Webhook FastAPI
```

---

## 💾 Respaldos de Base de Datos y Logs Locales (Makefile)

La base de datos SQLite activa (`data/openclaw.db`), los certificados y los logs (`data/logs/`, `data/agent_logs/`) están excluidos de Git para mantener el repositorio limpio y proteger información sensible de la interacción del agente.

Para respaldar y restaurar de forma local los datos del sistema, se pueden utilizar los comandos implementados en el `Makefile` (los archivos `.tar.gz` resultantes están excluidos de Git en `.gitignore` para evitar subidas accidentales a GitHub):

* **Respaldo de Base de Datos SQLite:**
  - `make backupdb`: Comprime la base de datos activa `data/openclaw.db` a `data/db_backup.tar.gz`.
  - `make restoredb`: Descomprime `data/db_backup.tar.gz` a `data/openclaw.db`.

* **Respaldo de Registros de Logs:**
  - `make backuplogs`: Crea un archivo comprimido `data/logs_backup.tar.gz` con el contenido de las carpetas de logs.
  - `make restorelogs`: Descomprime `data/logs_backup.tar.gz` en sus carpetas correspondientes.

---

## 🔐 Privacidad y Gestión de Llaves

- **Unified Fallback**: Si una API Key (ej. Gemini) se agota, el `llm_manager.py` hace switch automáticamente a llaves secundarias (Azure / OpenAI), garantizando la continuidad operativa sin romper los flujos asíncronos de Heart o Khai.
- **Protección de Salida**: Todas las Habilidades que generen multimedia o archivos tienen prohibido escribir fuera de `/app/data/public/`.

---

## 📄 Licencia
MIT License
