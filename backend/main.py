from pathlib import Path
from typing import List, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from grammer import improve_text
from summarizer import summarize_text

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL_NAME = "qwen2.5:3b"
MAX_HISTORY_MESSAGES = 20  # keep the prompt small so a 3B model stays fast

SYSTEM_PROMPT = (
    "You are LocalMind AI, an offline AI assistant. "
    "Answer questions clearly and naturally. "
    "Do not claim to access the internet or live information. "
    "Be honest when you do not know something."
)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = FastAPI(title="LocalMind AI")

# Allows the Vite dev server (port 5173) to call the API during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    # The browser sends the earlier turns, so the server stays stateless and
    # every LAN user gets their own separate conversation.
    history: List[HistoryMessage] = []

class GrammarRequest(BaseModel):
    text: str
    task: str = "grammar"

class SummarizeRequest(BaseModel):
    text: str = Field(min_length=1)
    length: Literal["short", "medium", "detailed"] = "medium"


async def call_ollama_errors(coro):
    """Turn Ollama failures into readable HTTP errors."""
    try:
        return await coro
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="Cannot reach Ollama. Start it with 'ollama serve' and make sure the model is pulled.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="The model took too long to respond. Try a shorter message.",
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama returned an error ({exc.response.status_code}). Is '{MODEL_NAME}' pulled?",
        )


@app.get("/api/health")
async def health():
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            ollama_ok = r.status_code == 200
    except httpx.HTTPError:
        pass
    return {
        "status": "LocalMind AI backend is running",
        "ollama": "reachable" if ollama_ok else "unreachable",
        "model": MODEL_NAME,
    }


async def _chat(request: ChatRequest) -> str:
    history = [m.model_dump() for m in request.history][-MAX_HISTORY_MESSAGES:]
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": request.message},
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        return response.json()["message"]["content"]


@app.post("/api/chat")
async def chat(request: ChatRequest):
    reply = await call_ollama_errors(_chat(request))
    return {"reply": reply}


@app.post("/api/summarize")
async def summarize(request: SummarizeRequest):
    summary = await call_ollama_errors(summarize_text(request.text, request.length))
    return {"summary": summary}
@app.post("/api/improve-text")
async def improve(request: GrammarRequest):
    improved = await call_ollama_errors(
        improve_text(request.text, request.task)
    )
    return {"improved_text": improved}

# Serve the built React app (frontend/dist) at "/". Must come after the API routes.
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:

    @app.get("/")
    def home():
        return {
            "message": "LocalMind AI backend is running. Build the frontend with 'npm run build' in /frontend to see the chat UI."
        }
