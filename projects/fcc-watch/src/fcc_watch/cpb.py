from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


CALLSIGN_RE = re.compile(r"^[KW][A-Z0-9]{2,6}(?:-[A-Z0-9]{1,4})?$")


@dataclass(frozen=True)
class NetworkEntry:
    name: str
    licensee: str | None
    state: str | None


@dataclass(frozen=True)
class CpbData:
    call_signs: set[str]
    networks: list[NetworkEntry]


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().split())


def is_call_sign(value: str) -> bool:
    candidate = _clean_text(value).upper()
    if " " in candidate or not candidate:
        return False
    return bool(CALLSIGN_RE.match(candidate))


def normalize_call_sign(value: str) -> str:
    return _clean_text(value).upper()


def load_cpb_grantees(path: Path) -> CpbData:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("cpb-grantees.csv is missing headers")

        call_signs: set[str] = set()
        networks: list[NetworkEntry] = []

        for row in reader:
            grantee_name = _clean_text(row.get("Grantee Name") or row.get("\ufeffGrantee Name"))
            licensee_name = _clean_text(row.get("Licensee Name"))
            state = _clean_text(row.get("Grantee State")) or None
            if not grantee_name:
                continue

            if is_call_sign(grantee_name):
                call_signs.add(normalize_call_sign(grantee_name))
            else:
                networks.append(NetworkEntry(grantee_name, licensee_name or None, state))

        return CpbData(call_signs=call_signs, networks=networks)


def summarize_cpb(cpb: CpbData) -> dict:
    return {
        "call_signs": len(cpb.call_signs),
        "networks": len(cpb.networks),
    }
