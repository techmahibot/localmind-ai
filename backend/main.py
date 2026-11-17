import io
import json
from pathlib import Path
from typing import List, Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from grammer import improve_text
from pypdf import PdfReader

from summarizer import summarize_document

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL_NAME = "qwen2.5:3b"
MAX_HISTORY_MESSAGES = 20  # keep the prompt small so a 3B model stays fast
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

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


def build_payload(request: ChatRequest, stream: bool) -> dict:
    history = [m.model_dump() for m in request.history][-MAX_HISTORY_MESSAGES:]
    return {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": request.message},
        ],
        "stream": stream,
    }


async def _chat(request: ChatRequest) -> str:
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(OLLAMA_URL, json=build_payload(request, stream=False))
        response.raise_for_status()
        return response.json()["message"]["content"]


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Non-streaming chat: returns the whole reply at once."""
    reply = await call_ollama_errors(_chat(request))
    return {"reply": reply}


async def _open_stream(payload: dict):
    """Connect to Ollama and check it answered, before any text is sent to the browser."""
    client = httpx.AsyncClient(timeout=180.0)
    try:
        response = await client.send(
            client.build_request("POST", OLLAMA_URL, json=payload), stream=True
        )
        response.raise_for_status()
        return client, response
    except BaseException:
        await client.aclose()
        raise


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """Streaming chat: sends the reply piece by piece as the model produces it."""
    client, response = await call_ollama_errors(
        _open_stream(build_payload(request, stream=True))
    )

    async def pieces():
        try:
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                text = data.get("message", {}).get("content", "")
                if text:
                    yield text
                if data.get("done"):
                    break
        except httpx.HTTPError:
            yield "\n\n[The connection to the model was lost.]"
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        pieces(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/summarize")
async def summarize(request: SummarizeRequest):
    return await call_ollama_errors(summarize_document(request.text, request.length))


def extract_text(filename: str, data: bytes) -> str:
    """Get plain text out of an uploaded .txt, .md or .pdf file."""
    name = filename.lower()
    if name.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="Could not read that PDF. It may be damaged or password protected.",
            )
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="ignore")
    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Please upload a .txt, .md or .pdf file.",
        )

    text = text.strip()
    if not text:
        raise HTTPException(
            status_code=422,
            detail="No readable text found in that file. Scanned PDFs (images of text) are not supported.",
        )
    return text


@app.post("/api/summarize/file")
async def summarize_file(
    file: UploadFile = File(...),
    length: Literal["short", "medium", "detailed"] = Form("medium"),
):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large. The limit is 5 MB.")
    text = extract_text(file.filename or "", data)
    result = await call_ollama_errors(summarize_document(text, length))
    return {**result, "filename": file.filename, "characters": len(text)}


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
