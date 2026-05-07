from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass
class State:
    last_run: datetime | None
    seen_items: set[str]


def load_state(path: Path) -> State:
    if not path.exists():
        return State(last_run=None, seen_items=set())

    data = json.loads(path.read_text(encoding="utf-8"))
    last_run_raw = data.get("last_run")
    last_run = (
        datetime.fromisoformat(last_run_raw)
        if isinstance(last_run_raw, str)
        else None
    )
    seen_items = set(data.get("seen_items", []))
    return State(last_run=last_run, seen_items=seen_items)


def save_state(path: Path, state: State) -> None:
    payload = {
        "last_run": state.last_run.isoformat() if state.last_run else None,
        "seen_items": sorted(state.seen_items),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def prune_seen_items(items: Iterable[str], max_items: int = 10000) -> set[str]:
    items_list = list(dict.fromkeys(items))
    if len(items_list) <= max_items:
        return set(items_list)
    return set(items_list[-max_items:])
