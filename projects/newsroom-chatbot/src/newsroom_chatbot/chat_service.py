from dataclasses import asdict
from typing import Any

from openai import OpenAI

from newsroom_chatbot.retrieval import RetrievedChunk


SYSTEM_PROMPT = """You are an internal newsroom research assistant.
Answer only using the provided source excerpts.
If evidence is weak or conflicting, say so explicitly.
Always cite source IDs in square brackets like [S1], [S2].
Do not fabricate facts.
If a source includes correction notes, treat corrected facts as authoritative over earlier wording.
When relevant, mention that a correction exists.
"""


def build_context(chunks: list[RetrievedChunk]) -> str:
    lines: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        source_id = f"S{idx}"
        date = chunk.published_at or "unknown-date"
        lines.append(f"[{source_id}] {chunk.title} ({date}) {chunk.url}\n{chunk.content}")
    return "\n\n".join(lines)


def answer_question(
    client: OpenAI,
    *,
    model: str,
    question: str,
    chunks: list[RetrievedChunk],
) -> dict[str, Any]:
    context = build_context(chunks)
    user_prompt = (
        "Question:\n"
        f"{question}\n\n"
        "Source excerpts:\n"
        f"{context}\n\n"
        "Write a concise newsroom-oriented answer with citations."
    )
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    text = response.output_text.strip()
    return {
        "answer": text,
        "sources": [
            {
                "source_id": f"S{idx}",
                **asdict(chunk),
            }
            for idx, chunk in enumerate(chunks, start=1)
        ],
    }
