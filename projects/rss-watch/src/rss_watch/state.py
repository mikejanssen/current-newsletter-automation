from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MAX_SEEN_IDS = 8000


@dataclass
class RunState:
    last_checked: datetime | None
    seen_ids: list[str]


def load_state(path: Path) -> RunState:
    if not path.exists():
        return RunState(last_checked=None, seen_ids=[])
    data = json.loads(path.read_text(encoding="utf-8"))
    last_checked_raw = data.get("last_checked")
    last_checked = datetime.fromisoformat(last_checked_raw) if last_checked_raw else None
    seen_ids = data.get("seen_ids") or []
    return RunState(last_checked=last_checked, seen_ids=seen_ids)


def save_state(path: Path, state: RunState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_checked": state.last_checked.astimezone(timezone.utc).isoformat() if state.last_checked else None,
        "seen_ids": state.seen_ids[-MAX_SEEN_IDS:],
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

