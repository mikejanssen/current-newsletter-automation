import argparse
import os
import random
import time
from typing import Sequence

from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, RateLimitError

from newsroom_chatbot.db import connect, replace_chunks, set_chunk_embedding
from newsroom_chatbot.text_utils import chunk_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create chunk embeddings for ingested articles")
    parser.add_argument("--db", default="output/newsroom.db", help="SQLite DB path")
    parser.add_argument(
        "--embedding-model",
        default="text-embedding-3-small",
        help="OpenAI embedding model",
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=0,
        help="Optional cap for trial runs",
    )
    parser.add_argument(
        "--skip-rechunk",
        action="store_true",
        help="Skip rebuilding chunks and only embed rows where embedding_json IS NULL",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Embedding batch size",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=8,
        help="Retries per embedding batch for transient API errors",
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=1.0,
        help="Base delay used for exponential backoff",
    )
    return parser.parse_args()


def get_client() -> OpenAI:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    return OpenAI()


def embed_batch(client: OpenAI, model: str, inputs: Sequence[str]) -> list[list[float]]:
    response = client.embeddings.create(model=model, input=list(inputs))
    return [item.embedding for item in response.data]

def embed_batch_with_retry(
    client: OpenAI,
    *,
    model: str,
    inputs: Sequence[str],
    max_retries: int,
    retry_base_seconds: float,
) -> list[list[float]]:
    for attempt in range(max_retries + 1):
        try:
            return embed_batch(client, model, inputs)
        except (RateLimitError, APIConnectionError, APITimeoutError) as err:
            if attempt >= max_retries:
                raise
            delay = retry_base_seconds * (2**attempt) + random.uniform(0.0, 0.5)
            print(
                f"embedding batch retry {attempt + 1}/{max_retries} after "
                f"{type(err).__name__}; sleeping {delay:.2f}s"
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


def main() -> None:
    args = parse_args()
    conn = connect(args.db)
    client = get_client()

    if args.skip_rechunk:
        print("skip_rechunk enabled: preserving existing chunks")
    else:
        rows = conn.execute("SELECT id, title, text FROM articles ORDER BY id").fetchall()
        for article in rows:
            chunks = list(chunk_text(article["text"]))
            replace_chunks(conn, article_id=int(article["id"]), chunks=chunks)
            conn.commit()
            print(f"chunked article {article['id']}: {article['title']} ({len(chunks)} chunks)")

    chunk_rows = conn.execute(
        "SELECT id, content FROM chunks WHERE embedding_json IS NULL ORDER BY id"
    ).fetchall()
    if args.max_chunks:
        chunk_rows = chunk_rows[: args.max_chunks]
    print(f"chunks to embed: {len(chunk_rows)}")
    if not chunk_rows:
        conn.close()
        print("done")
        return

    batch_size = args.batch_size
    for start in range(0, len(chunk_rows), batch_size):
        batch = chunk_rows[start : start + batch_size]
        texts = [r["content"] for r in batch]
        vectors = embed_batch_with_retry(
            client,
            model=args.embedding_model,
            inputs=texts,
            max_retries=args.max_retries,
            retry_base_seconds=args.retry_base_seconds,
        )
        for row, vector in zip(batch, vectors):
            set_chunk_embedding(conn, int(row["id"]), vector)
        conn.commit()
        print(f"embedded {start + len(batch)}/{len(chunk_rows)}")

    conn.close()
    print("done")


if __name__ == "__main__":
    main()
