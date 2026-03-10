from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256


@dataclass(frozen=True)
class StationRecord:
    station_id: str
    station_name: str
    page_url: str = ""
    notes: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class AuditDocument:
    station_id: str
    station_name: str
    discovered_date: date
    page_url: str
    document_url: str
    title: str
    file_ext: str
    status: str
    confidence: str
    downloaded_path: str = ""
    content_sha256: str = ""
    flags: str = ""
    summary: str = ""

    @property
    def doc_id(self) -> str:
        payload = "|".join(
            [
                self.station_id,
                self.document_url,
                self.title,
                self.file_ext,
            ]
        )
        return sha256(payload.encode("utf-8")).hexdigest()[:20]
