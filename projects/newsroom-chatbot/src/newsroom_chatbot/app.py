import os
import secrets
from base64 import b64decode
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel, Field

from newsroom_chatbot.db import connect
from newsroom_chatbot.chat_service import answer_question
from newsroom_chatbot.retrieval import search_chunks


ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
DEFAULT_DB_PATH = str(ROOT / "output" / "newsroom.db")

app = FastAPI(title="Newsroom Chatbot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


class ChatRequest(BaseModel):
    question: str = Field(min_length=3)
    top_k: int = Field(default=8, ge=1, le=20)
    date_from: str | None = None
    date_to: str | None = None
    model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"


def get_db_path() -> str:
    return os.environ.get("NEWSROOM_DB_PATH", DEFAULT_DB_PATH)


def get_client() -> OpenAI:
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not set")
    return OpenAI()


def _parse_basic_auth(authorization: str | None) -> tuple[str, str] | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "basic" or not token:
        return None
    try:
        decoded = b64decode(token).decode("utf-8")
    except Exception:
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


@app.middleware("http")
async def basic_auth_gate(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path == "/healthz":
        return await call_next(request)

    auth_user = os.environ.get("NEWSROOM_AUTH_USERNAME")
    auth_pass = os.environ.get("NEWSROOM_AUTH_PASSWORD")
    auth_enabled = bool(auth_user or auth_pass)

    if not auth_enabled:
        return await call_next(request)
    if not auth_user or not auth_pass:
        return JSONResponse(
            status_code=500,
            content={
                "detail": (
                    "Auth misconfigured: set both NEWSROOM_AUTH_USERNAME "
                    "and NEWSROOM_AUTH_PASSWORD."
                )
            },
        )

    parsed = _parse_basic_auth(request.headers.get("Authorization"))
    valid = (
        parsed is not None
        and secrets.compare_digest(parsed[0], auth_user)
        and secrets.compare_digest(parsed[1], auth_pass)
    )
    if valid:
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required."},
        headers={"WWW-Authenticate": "Basic"},
    )


@app.get("/")
def home() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    db = connect(get_db_path())
    article_count = db.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
    chunk_count = db.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    embedded_count = db.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE embedding_json IS NOT NULL"
    ).fetchone()["n"]
    db.close()
    return {
        "ok": True,
        "db_path": get_db_path(),
        "articles": article_count,
        "chunks": chunk_count,
        "embedded_chunks": embedded_count,
    }


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    db = connect(get_db_path())
    client = get_client()

    query_embedding = client.embeddings.create(
        model=request.embedding_model,
        input=[request.question],
    ).data[0].embedding

    chunks = search_chunks(
        db,
        query_embedding=query_embedding,
        top_k=request.top_k,
        date_from=request.date_from,
        date_to=request.date_to,
    )
    db.close()

    if not chunks:
        return {
            "answer": "I couldn't find enough indexed coverage to answer that yet.",
            "sources": [],
        }

    return answer_question(
        client,
        model=request.model,
        question=request.question,
        chunks=chunks,
    )
