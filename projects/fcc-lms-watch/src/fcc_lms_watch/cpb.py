from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path


CALLSIGN_RE = re.compile(r"^[KW][A-Z0-9]{2,6}(?:-[A-Z0-9]{1,4})?$")


@dataclass(frozen=True)
class CpbData:
    call_signs: set[str]
    facility_ids: set[str]
    org_names: set[str]


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().split())


def normalize_org_name(value: str | None) -> str:
    raw = _clean_text(value).lower()
    if not raw:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", " ", raw)
    tokens = [t for t in cleaned.split() if t and t not in {
        "the",
        "and",
        "of",
        "for",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "company",
        "co",
        "llc",
        "ltd",
        "trust",
    }]
    return " ".join(tokens)


def is_call_sign(value: str) -> bool:
    candidate = _clean_text(value).upper()
    if " " in candidate or not candidate:
        return False
    return bool(CALLSIGN_RE.match(candidate))


def normalize_call_sign(value: str) -> str:
    return _clean_text(value).upper()


def call_sign_variants(value: str) -> set[str]:
    base = normalize_call_sign(value)
    variants = {base}
    if base.endswith("-FM") or base.endswith("-AM") or base.endswith("-TV"):
        variants.add(base.rsplit("-", 1)[0])
    return variants


def load_aliases(path: Path | None) -> tuple[set[str], set[str], set[str]]:
    if not path or not path.exists():
        return set(), set(), set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return set(), set(), set()

    alias_call_signs: set[str] = set()
    alias_facility_ids: set[str] = set()
    alias_org_names: set[str] = set()

    for value in data.get("call_signs", []):
        if isinstance(value, str) and is_call_sign(value):
            alias_call_signs.update(call_sign_variants(value))
    for value in data.get("facility_ids", []):
        if isinstance(value, str) and value.strip():
            alias_facility_ids.add(value.strip())
    for value in data.get("org_names", []):
        if isinstance(value, str):
            norm = normalize_org_name(value)
            if norm:
                alias_org_names.add(norm)

    return alias_call_signs, alias_facility_ids, alias_org_names


def load_cpb_grantees(path: Path, aliases_path: Path | None = None) -> CpbData:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("cpb-grantees.csv is missing headers")

        call_signs: set[str] = set()
        facility_ids: set[str] = set()
        org_names: set[str] = set()

        for row in reader:
            grantee_name = _clean_text(row.get("Grantee Name") or row.get("\ufeffGrantee Name"))
            if not grantee_name:
                continue
            if is_call_sign(grantee_name):
                call_signs.update(call_sign_variants(grantee_name))
            else:
                norm_grantee = normalize_org_name(grantee_name)
                if norm_grantee:
                    org_names.add(norm_grantee)

            licensee_name = _clean_text(row.get("Licensee Name"))
            if licensee_name:
                norm_licensee = normalize_org_name(licensee_name)
                if norm_licensee:
                    org_names.add(norm_licensee)

        alias_call_signs, alias_facility_ids, alias_org_names = load_aliases(aliases_path)
        call_signs.update(alias_call_signs)
        facility_ids.update(alias_facility_ids)
        org_names.update(alias_org_names)

        return CpbData(call_signs=call_signs, facility_ids=facility_ids, org_names=org_names)
