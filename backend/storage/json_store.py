"""
Simple JSON file storage — acceptable per Assignment 2 spec.
Each collection is one JSON file: a dict keyed by record ID.
Thread-safety: FastAPI runs single-threaded by default with uvicorn,
so file locking is not required for the dev/demo context.
"""
import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def _path(collection: str) -> Path:
    return DATA_DIR / f"{collection}.json"


def load(collection: str) -> dict[str, Any]:
    p = _path(collection)
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def save(collection: str, data: dict[str, Any]) -> None:
    with open(_path(collection), "w") as f:
        json.dump(data, f, indent=2)


def get(collection: str, record_id: str) -> dict | None:
    return load(collection).get(record_id)


def put(collection: str, record_id: str, record: dict) -> None:
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


def all_values(collection: str) -> list[dict]:
    return list(load(collection).values())
