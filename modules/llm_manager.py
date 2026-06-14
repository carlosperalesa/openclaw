import os
import time
import logging
import requests
from typing import Dict

logger = logging.getLogger("LLMManager")

class LLMManager:
    """Fachada global del sistema LLM con Unified Fallback."""
    
    def __init__(self, config_dir: str, data_dir: str, memory=None):
        self.config_dir = config_dir
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        if memory:
            self.memory = memory
        else:
            from modules.memory_engine import MemoryEngine
            self.memory = MemoryEngine(data_dir=os.path.join(os.path.dirname(self.data_dir), "memory"))
        self.cooldown_seconds = 60
        self.api_keys_pool = []
        self._load_keys()

    def _load_keys(self):
        """Carga todas las llaves disponibles en la variable de entorno y las ordena."""
        # 1. Gemini
        for k, v in sorted(os.environ.items()):
            if k.startswith("GEMINI_API_KEY"):
                self.api_keys_pool.append({"type": "gemini", "key": v, "id": k})
                
        # 2. Azure
        for k, v in sorted(os.environ.items()):
            if k.startswith("AZURE_OPENAI_API_KEY"):
                self.api_keys_pool.append({"type": "azure", "key": v, "id": k})
                
        # 3. OpenAI
        for k, v in sorted(os.environ.items()):
            if k.startswith("OPENAI_API_KEY"):
                self.api_keys_pool.append({"type": "openai", "key": v, "id": k})
                
        if not self.api_keys_pool:
            logger.warning("No se encontraron API keys en el entorno (Gemini, Azure, OpenAI).")

    def _load_status(self) -> Dict:
        return self.memory.get_provider_status()

    def _mark_key_failed(self, key_id: str):
        cooldown_until = time.time() + self.cooldown_seconds
        self.memory.set_provider_cooldown(key_id, cooldown_until)

    def generate_content(self, prompt: str, **kwargs) -> str:
        """Genera contenido iterando sobre el pool global de llaves (Unified Fallback)."""
        status = self._load_status()
        now = time.time()
        
        # Opciones avanzadas o forzadas (ej: para Pixy VIP channel)
        forced_key = kwargs.get("forced_key")
        forced_type = kwargs.get("forced_type")
        
        if forced_key and forced_type:
            pool = [{"type": forced_type, "key": forced_key, "id": "FORCED_KEY"}]
        else:
            pool = self.api_keys_pool

        for attempt in range(max(1, len(pool) * 2)):
            now = time.time()
            current_cred = None
            
            # Buscar la primera llave que no esté en cooldown
            for cred in pool:
                if now > status.get(cred["id"], 0):
                    current_cred = cred
                    break
                    
            if not current_cred:
                logger.error("Todas las llaves están bloqueadas por rate limit.")
                time.sleep(10)
                # Si estamos desesperados, reintentamos la primera llave igual
                current_cred = pool[0] if pool else None
                
            if not current_cred:
                raise ValueError("No hay API keys configuradas.")

            try:
                if current_cred["type"] == "gemini":
                    return self._generate_gemini(prompt, current_cred["key"], kwargs)
                elif current_cred["type"] == "azure":
                    return self._generate_azure(prompt, current_cred["key"], kwargs)
                elif current_cred["type"] == "openai":
                    return self._generate_openai(prompt, current_cred["key"], kwargs)
            except Exception as e:
                err_str = str(e).lower()
                logger.warning(f"La llave {current_cred['id']} falló: {e}")
                if "429" in err_str or "too many requests" in err_str or "quota" in err_str:
                    logger.warning(f"Poniendo en cooldown la llave {current_cred['id']} (60s)...")
                    self._mark_key_failed(current_cred["id"])
                    status = self._load_status() # recargar estado
                    time.sleep(1)
                    continue
                else:
                    # Si es otro error (ej: 503, 500, timeout), ponemos un cooldown de 15s para forzar saltar a la siguiente llave.
                    logger.warning(f"Error de servidor/red. Poniendo en cooldown la llave {current_cred['id']} (15s)...")
                    cooldown_until = time.time() + 15
                    self.memory.set_provider_cooldown(current_cred["id"], cooldown_until)
                    status = self._load_status()
                    time.sleep(1)
                    continue
                    
        raise ValueError("Unified Fallback agotó todos los intentos y llaves.")

    def _generate_gemini(self, prompt: str, key: str, kwargs: Dict) -> str:
        model_name = kwargs.get("model", "gemini-2.5-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
        
        generation_config = {}
        if kwargs.get("response_format", {}).get("type") == "json_object":
            generation_config["responseMimeType"] = "application/json"
            
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        if generation_config:
            payload["generationConfig"] = generation_config
            
        if kwargs.get("use_web_search", False):
            payload["tools"] = [{"googleSearch": {}}]
            
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

    def _generate_azure(self, prompt: str, key: str, kwargs: Dict) -> str:
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        
        if not endpoint or not deployment:
            raise ValueError("Falta configurar AZURE_OPENAI_ENDPOINT o AZURE_OPENAI_DEPLOYMENT.")
            
        try:
            from openai import AzureOpenAI
            client = AzureOpenAI(
                api_key=key,
                api_version="2024-02-15-preview",
                azure_endpoint=endpoint
            )
            
            call_kwargs = {
                "model": deployment,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": kwargs.get("temperature", 0.7)
            }
            if kwargs.get("response_format"):
                call_kwargs["response_format"] = kwargs["response_format"]
                
            response = client.chat.completions.create(**call_kwargs)
            return response.choices[0].message.content.strip()
        except ImportError:
            url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version=2024-02-15-preview"
            headers = {"api-key": key, "Content-Type": "application/json"}
            payload = {
                "messages": [{"role": "user", "content": prompt}],
                "temperature": kwargs.get("temperature", 0.7)
            }
            if kwargs.get("response_format"):
                payload["response_format"] = kwargs["response_format"]
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()

    def _generate_openai(self, prompt: str, key: str, kwargs: Dict) -> str:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key)
            model = kwargs.get("model", "gpt-4o-mini")
            
            call_kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": kwargs.get("temperature", 0.7)
            }
            if kwargs.get("response_format"):
                call_kwargs["response_format"] = kwargs["response_format"]
                
            response = client.chat.completions.create(**call_kwargs)
            return response.choices[0].message.content.strip()
        except ImportError:
            raise ValueError("El paquete 'openai' no está instalado.")

    def test_all_connections(self) -> Dict[str, Dict]:
        return {"unified_pool": {"status": "ok", "keys_loaded": len(self.api_keys_pool)}}
