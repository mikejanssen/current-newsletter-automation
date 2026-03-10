from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from audit_chatbot.db import connect, init_schema, rebuild_fts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest archived audit docs into audit-chatbot DB")
    p.add_argument("--db", default="output/audit-chatbot.db")
    p.add_argument("--archive-root", default="../audit-watch/output/audits")
    p.add_argument("--stations", default="../audit-watch/config/stations.csv")
    p.add_argument("--max-text-chars", type=int, default=200000)
    return p.parse_args(argv)


def load_station_rows(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fixed = {(k or "").lstrip("\ufeff"): (v or "") for k, v in row.items()}
            sid = fixed.get("station_id", "").strip()
            name = fixed.get("station_name", "").strip()
            page_url = fixed.get("page_url", "").strip()
            notes = fixed.get("notes", "").strip()
            if sid:
                out[sid] = {
                    "station_name": name or sid,
                    "page_url": page_url,
                    "notes": notes,
                }
    return out


def normalize_url(url: str) -> str:
    text = url.strip().lower()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    text = text.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return text


def canonical_rank(name: str) -> tuple[int, int, str]:
    n = name.lower()
    legal_markers = [
        "foundation",
        "inc",
        "corporation",
        "authority",
        "commission",
        "school board",
        "county",
        "educational",
        "television",
    ]
    penalty = sum(1 for m in legal_markers if m in n)
    return (penalty, len(name), n)


def build_canonical_map(rows: dict[str, dict[str, str]]) -> dict[str, tuple[str, str]]:
    by_url: dict[str, list[str]] = defaultdict(list)
    for sid, row in rows.items():
        key = normalize_url(row.get("page_url", ""))
        if key:
            by_url[key].append(sid)

    canonical_map: dict[str, tuple[str, str]] = {}
    for station_ids in by_url.values():
        station_ids.sort(key=lambda sid: canonical_rank(rows[sid]["station_name"]))
        can_id = station_ids[0]
        can_name = rows[can_id]["station_name"]
        for sid in station_ids:
            canonical_map[sid] = (can_id, can_name)

    for sid, row in rows.items():
        canonical_map.setdefault(sid, (sid, row["station_name"]))
    return canonical_map


def title_from_filename(name: str) -> str:
    stem = Path(name).stem
    text = re.sub(r"[_-]+", " ", stem)
    text = re.sub(r"\s+", " ", text).strip()
    return text or name


def extract_text(path: Path, max_chars: int) -> tuple[str, bool]:
    try:
        proc = subprocess.run(
            ["pdftotext", "-q", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout:
            text = proc.stdout[:max_chars]
            return text, True
    except FileNotFoundError:
        pass

    # Fallback: binary strings extraction (lower quality).
    try:
        proc = subprocess.run(
            ["strings", str(path)],
            check=False,
            capture_output=True,
            text=True,
            errors="ignore",
        )
        text = (proc.stdout or "")[:max_chars]
        return text, False
    except Exception:
        return "", False


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    archive_root = Path(args.archive_root)
    station_rows = load_station_rows(Path(args.stations))
    canonical_map = build_canonical_map(station_rows)
    db_path = Path(args.db)

    if not archive_root.exists():
        print(f"Archive root not found: {archive_root}")
        return 2

    if db_path.exists():
        db_path.unlink()

    conn = connect(args.db)
    init_schema(conn)

    files = [p for p in archive_root.rglob("*.pdf") if p.is_file()]

    inserted = 0
    updated = 0
    deduped = 0
    seen_station_hashes: set[tuple[str, str]] = set()
    for path in files:
        rel = path.relative_to(archive_root)
        if len(rel.parts) < 2:
            continue
        station_id = rel.parts[0]
        station_name = station_rows.get(station_id, {}).get("station_name", station_id)
        canonical_station_id, canonical_station_name = canonical_map.get(
            station_id, (station_id, station_name)
        )
        st = path.stat()
        title = title_from_filename(path.name)
        content_text, extracted_ok = extract_text(path, args.max_text_chars)
        sha = file_sha256(path)
        dedupe_key = (canonical_station_id, sha)
        if dedupe_key in seen_station_hashes:
            deduped += 1
            continue
        seen_station_hashes.add(dedupe_key)
        doc_id = hashlib.sha256(f"{canonical_station_id}|{sha}".encode("utf-8")).hexdigest()[:24]

        cur = conn.execute("SELECT 1 FROM docs WHERE doc_id = ?", (doc_id,))
        exists = cur.fetchone() is not None
        conn.execute(
            """
            INSERT OR REPLACE INTO docs(
              doc_id, station_id, station_name, canonical_station_id, canonical_station_name,
              title, file_path, file_name, file_sha256, content_text,
              extracted_ok, file_mtime, file_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                station_id,
                station_name,
                canonical_station_id,
                canonical_station_name,
                title,
                path.as_posix(),
                path.name,
                sha,
                content_text,
                1 if extracted_ok else 0,
                float(st.st_mtime),
                int(st.st_size),
            ),
        )
        if exists:
            updated += 1
        else:
            inserted += 1

    rebuild_fts(conn)

    print(f"PDF files seen: {len(files)}")
    print(f"Inserted docs: {inserted}")
    print(f"Updated docs: {updated}")
    print(f"Deduped docs skipped: {deduped}")
    print(f"DB: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
