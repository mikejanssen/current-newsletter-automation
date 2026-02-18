from typing import Iterable


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def chunk_text(text: str, *, max_chars: int = 1400, overlap_chars: int = 220) -> Iterable[str]:
    clean = normalize_whitespace(text)
    if not clean:
        return []

    chunks: list[str] = []
    start = 0
    length = len(clean)
    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            split = clean.rfind(" ", start, end)
            if split > start + 300:
                end = split
        chunks.append(clean[start:end].strip())
        if end >= length:
            break
        start = max(0, end - overlap_chars)
    return chunks
