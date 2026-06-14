import sqlite3
import os
import glob

db_path = "/app/data/openclaw.db"
skills_dir = "/app/data/skills/"

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM skills_library")
    conn.commit()
    conn.close()
    print("DB purgata.")

for f in glob.glob(os.path.join(skills_dir, "*.py")):
    os.remove(f)
print("Skills eliminadas.")
