from datetime import datetime

users = {}
documents = {}
refresh_tokens = {}


def now_iso():
    return datetime.utcnow().isoformat()