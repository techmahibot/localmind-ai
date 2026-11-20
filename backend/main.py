import os
from pathlib import Path
from typing import List, Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from grammer import improve_text
from documents import extract_text, router as documents_router

from summarizer import summarize_document
from llm import (ModelConfigurationError, ModelResponseError, ask, available_models,
                 model_name, open_stream, reachable, resolve_model, stream_content)
from auth import (
    AuthConfigurationError, DATABASE_ERRORS, UsernameTaken, SESSION_SECONDS, create_session,
    database_ready, find_user, find_user_by_id, register, session_secret, session_user_id,
    verify_password,
)

MAX_HISTORY_MESSAGES = 20  # keep the prompt small so a 3B model stays fast
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_MESSAGE_CHARS = 12000
SESSION_COOKIE = "localmind_session"

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
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(documents_router)


class LoginRequest(BaseModel):
    username: str
    password: str


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/health", "/api/auth/login", "/api/auth/signup"):
        try:
            user_id = session_user_id(request.cookies.get(SESSION_COOKIE))
        except AuthConfigurationError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=503)
        if not user_id:
            return JSONResponse({"detail": "Please sign in to continue."}, status_code=401)
        request.state.user_id = user_id
    return await call_next(request)


def set_session(response: Response, user_id: int):
    response.set_cookie(
        SESSION_COOKIE, create_session(user_id), max_age=SESSION_SECONDS,
        httponly=True, samesite="lax", secure=bool(os.environ.get("VERCEL")), path="/",
    )


@app.post("/api/auth/signup", status_code=201)
async def signup(credentials: LoginRequest, response: Response):
    try:
        session_secret()
        user_id, username = register(credentials.username, credentials.password)
        set_session(response, user_id)
    except AuthConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except UsernameTaken:
        raise HTTPException(409, "This username is already taken.")
    except DATABASE_ERRORS:
        raise HTTPException(503, "Account storage is temporarily unavailable.")
    return {"username": username}


@app.post("/api/auth/login")
async def login(credentials: LoginRequest, response: Response):
    try:
        session_secret()
        user = find_user(credentials.username)
    except AuthConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except DATABASE_ERRORS:
        raise HTTPException(503, "Account storage is temporarily unavailable.")
    if not user or not verify_password(credentials.password, user[2]):
        raise HTTPException(401, "Incorrect username or password.")
    set_session(response, user[0])
    return {"username": user[1]}


@app.get("/api/auth/me")
async def current_user(request: Request):
    try:
        user = find_user_by_id(request.state.user_id)
    except AuthConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except DATABASE_ERRORS:
        raise HTTPException(503, "Account storage is temporarily unavailable.")
    if user is None:
        raise HTTPException(401, "Please sign in to continue.")
    return {"username": user[1]}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    model: str = Field(default="", max_length=120)
    # The browser sends the earlier turns, so the server stays stateless and
    # every LAN user gets their own separate conversation.
    history: List[HistoryMessage] = Field(default_factory=list, max_length=100)

class GrammarRequest(BaseModel):
    text: str = Field(min_length=1, max_length=48000)
    task: Literal["grammar", "formal", "simple", "concise", "reformat"] = "grammar"
    model: str = Field(default="", max_length=120)

class SummarizeRequest(BaseModel):
    text: str = Field(min_length=1)
    length: Literal["short", "medium", "detailed"] = "medium"
    mode: Literal["general", "science", "news"] = "general"
    topic: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=120)


async def call_model_errors(coro):
    """Turn model configuration and transport failures into readable HTTP errors."""
    try:
        return await coro
    except ModelConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ModelResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="Cannot reach the model server. Check the configured model endpoint.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="The model took too long to respond. Try a shorter message.",
        )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="The model server connection failed.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The model server returned an error ({exc.response.status_code}). Check its model name and credentials.",
        )


@app.get("/api/health")
async def health():
    model_ok = False
    config_error = None
    try:
        model_ok = await reachable()
    except ModelConfigurationError as exc:
        config_error = str(exc)
    except httpx.HTTPError:
        pass
    storage_ok = False
    try:
        storage_ok = database_ready()
    except (AuthConfigurationError, *DATABASE_ERRORS, OSError):
        pass
    return {
        "status": "LocalMind AI backend is running",
        "model_server": "reachable" if model_ok else "unavailable",
        "model": model_name(),
        "deployment": "hosted" if (os.environ.get("VERCEL") or
                                    os.environ.get("LLM_PROVIDER", "ollama").lower() == "openai") else "local",
        "configuration_error": config_error,
        "account_storage": "reachable" if storage_ok else "unavailable",
        "login_configured": bool(storage_ok and
                                 (os.environ.get("LOCALMIND_SESSION_SECRET") or not os.environ.get("VERCEL"))),
    }


@app.get("/api/models")
async def models():
    names = await call_model_errors(available_models())
    return {"models": names, "default": model_name()}


async def _chat(request: ChatRequest) -> str:
    selected = await resolve_model(request.model)
    return await ask(build_messages(request), selected_model=selected)


def build_messages(request: ChatRequest):
    history = [
        {"role": m.role, "content": m.content[:MAX_MESSAGE_CHARS]}
        for m in request.history[-MAX_HISTORY_MESSAGES:]
    ]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": request.message.strip()},
    ]


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Non-streaming chat: returns the whole reply at once."""
    if not request.message.strip():
        raise HTTPException(422, "Message cannot be blank.")
    reply = await call_model_errors(_chat(request))
    return {"reply": reply}


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """Streaming chat: sends the reply piece by piece as the model produces it."""
    if not request.message.strip():
        raise HTTPException(422, "Message cannot be blank.")
    selected = await call_model_errors(resolve_model(request.model))
    client, response = await call_model_errors(
        open_stream(build_messages(request), selected_model=selected)
    )

    async def pieces():
        finished = False
        try:
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    text, done = stream_content(line)
                except (ValueError, KeyError, IndexError):
                    continue
                if text:
                    yield text
                if done:
                    finished = True
                    break
            if not finished:
                yield "\n\n[The model stream ended unexpectedly.]"
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
    if not request.text.strip():
        raise HTTPException(422, "Text cannot be blank.")
    selected = await call_model_errors(resolve_model(request.model))
    return await call_model_errors(
        summarize_document(request.text, request.length, request.mode, request.topic, selected)
    )


@app.post("/api/summarize/file")
async def summarize_file(
    file: UploadFile = File(...),
    length: Literal["short", "medium", "detailed"] = Form("medium"),
    mode: Literal["general", "science", "news"] = Form("general"),
    topic: str = Form("", max_length=120),
    model: str = Form("", max_length=120),
):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large. The limit is 5 MB.")
    text = extract_text(file.filename or "", data)
    selected = await call_model_errors(resolve_model(model))
    result = await call_model_errors(summarize_document(text, length, mode, topic, selected))
    return {**result, "filename": file.filename, "characters": len(text)}


@app.post("/api/improve-text")
async def improve(request: GrammarRequest):
    if not request.text.strip():
        raise HTTPException(422, "Text cannot be blank.")
    selected = await call_model_errors(resolve_model(request.model))
    improved = await call_model_errors(improve_text(request.text, request.task, selected))
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
