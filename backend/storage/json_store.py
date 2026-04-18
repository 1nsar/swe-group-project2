"""
Simple JSON file storage — acceptable per Assignment 2 spec.
Each collection is one JSON file: a dict keyed by record ID.
Thread-safety: FastAPI runs single-threaded by default with uvicorn,
so file locking is not required for the dev/demo context.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def _path(collection: str) -> Path:
    return DATA_DIR / f"{collection}.json"


def load(collection: str) -> Dict[str, Any]:
    p = _path(collection)
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def save(collection: str, data: Dict[str, Any]) -> None:
    with open(_path(collection), "w") as f:
        json.dump(data, f, indent=2)


def get(collection: str, record_id: str) -> Optional[Dict]:
    return load(collection).get(record_id)


def put(collection: str, record_id: str, record: Dict) -> None:
    data = load(collection)
    data[record_id] = record
    save(collection, data)


def delete(collection: str, record_id: str) -> bool:
    data = load(collection)
    if record_id not in data:
        return False
    del data[record_id]
    save(collection, data)
    return True


def all_values(collection: str) -> List[Dict]:
    return list(load(collection).values())
