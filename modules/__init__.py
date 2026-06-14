import os
from .libs.telegram_handler import TelegramHandler

class ModuleManager:
    """Punto de entrada para todos los módulos del sistema."""

    def __init__(self, config_dir="config", data_dir="data"):
        import yaml
        from dotenv import load_dotenv
        
        load_dotenv()
        
        with open(os.path.join(config_dir, "config.yaml"), "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            
        telegram = TelegramHandler(
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            user_id=os.getenv("TELEGRAM_USER_ID", "")
        )

        from .memory_engine import MemoryEngine
        from .llm_manager import LLMManager
        from .skill_forge import SkillForge
        from .core_mind import CoreMind
        from .core_heart import CoreHeart
        from .state_manager import StateManager
        from .pixy import PixyMind
        from .core_eyes import CoreEyes

        # MemoryEngine PRIMERO — es la fuente única de verdad para SQLite
        self.memory = MemoryEngine(data_dir=os.path.join(data_dir, "memory"))
        self.telegram = telegram
        # LLMManager recibe la instancia compartida de MemoryEngine (BUG #5 fix)
        self.llm = LLMManager(config_dir=config_dir, data_dir=os.path.join(data_dir, "llm"), memory=self.memory)
        self.state = StateManager(data_dir=os.path.join(data_dir, "memory"))
        self.forge = SkillForge(llm=self.llm, data_dir=os.path.join(data_dir, "skills"))
        self.forge.manager = self
        self.core_mind = CoreMind(llm=self.llm, memory=self.memory)
        self.core_eyes = CoreEyes(data_dir=data_dir)
        self.pixy = PixyMind(self)
        self.core_heart = CoreHeart(
            llm=self.llm,
            memory=self.memory,
            telegram=telegram,
            data_dir=data_dir,
            state_manager=self.state,
            pixy=self.pixy
        )
