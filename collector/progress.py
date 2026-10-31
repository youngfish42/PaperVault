import json
import os

COLLECT_PROGRESS_FILE = "cache/collect_progress.json"
COLLECT_FAILURES_FILE = "cache/collect_failures.json"


def load_collect_progress():
    if not os.path.exists(COLLECT_PROGRESS_FILE):
        return {}
    with open(COLLECT_PROGRESS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("completed", {})


def save_collect_progress(progress):
    os.makedirs(os.path.dirname(COLLECT_PROGRESS_FILE), exist_ok=True)
    with open(COLLECT_PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "completed": progress}, f, ensure_ascii=False, indent=2)
