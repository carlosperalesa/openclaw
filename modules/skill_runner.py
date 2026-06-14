import sys
import os
import importlib.util
from modules.state_manager import StateManager
from modules.libs.telegram_handler import TelegramHandler
from modules.skill_forge import BaseSkill

def run():
    if len(sys.argv) < 4:
        print("Uso: python skill_runner.py <task_id> <file_path> <request>")
        sys.exit(1)
        
    task_id = sys.argv[1]
    file_path = sys.argv[2]
    request = sys.argv[3]
    
    # Instantiate standalone instances for the worker
    state = StateManager(data_dir="data")
    telegram = TelegramHandler(bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""), user_id=os.environ.get("TELEGRAM_USER_ID", ""))
    
    try:
        spec = importlib.util.spec_from_file_location("dynamic_skill", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        skill_instance: BaseSkill = module.DynamicSkill(task_id, state, telegram)
        result = skill_instance.execute(request)
        
        print(f"RESULTADO_OK:{result}")
        sys.exit(0)
    except Exception as e:
        print(f"ERROR:{e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    run()
