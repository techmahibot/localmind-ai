
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
from llm import (
    ModelConfigurationError,
    ModelResponseError,
    ask,
    model_name,
    open_stream,
    reachable,
    stream_content,
)
from chat_storage import (
    initialize_chat_storage,
    create_conversation,
    list_conversations,
    get_messages,
    delete_conversation,
    save_message,
)
from auth import (
    AuthConfigurationError,
    DATABASE_ERRORS,
    UsernameTaken,
    SESSION_SECONDS,
    create_session,
    database_ready,
    find_user,
    find_user_by_id,
    register,
    session_secret,
    session_user_id,
    verify_password,
)


MAX_HISTORY_MESSAGES = 20
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(documents_router)


@app.on_event("startup")
async def initialize_database():
    database_ready()
    initialize_chat_storage()


# -------------------------------------------------------------------
# Conversation APIs
# -------------------------------------------------------------------

class CreateConversationRequest(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)
    conversation_type: Literal["chat", "document"] = "chat"
    document_name: str | None = Field(default=None, max_length=255)


@app.post("/api/conversations", status_code=201)
async def create_conversation_api(
    request: CreateConversationRequest,
    http: Request,
):
    title = request.title.strip()

    if not title:
        raise HTTPException(422, "Conversation title cannot be blank.")

    try:
        conversation_id = create_conversation(
            user_id=http.state.user_id,
            title=title,
            conversation_type=request.conversation_type,
            document_name=request.document_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except DATABASE_ERRORS:
        raise HTTPException(503, "Conversation storage is unavailable.")

    return {"id": conversation_id}


@app.get("/api/conversations")
async def list_conversations_api(
    request: Request,
    conversation_type: Literal["chat", "document"] | None = None,
):
    try:
        conversations = list_conversations(
            request.state.user_id,
            conversation_type,
        )
        return {"conversations": conversations}
    except DATABASE_ERRORS:
        raise HTTPException(503, "Conversation storage is unavailable.")


@app.get("/api/conversations/{conversation_id}/messages")
async def get_conversation_messages_api(
    conversation_id: int,
    request: Request,
):
    try:
        messages = get_messages(
            request.state.user_id,
            conversation_id,
        )
    except DATABASE_ERRORS:
        raise HTTPException(503, "Conversation storage is unavailable.")

    if messages is None:
        raise HTTPException(404, "Conversation not found.")

    return {"messages": messages}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation_api(
    conversation_id: int,
    request: Request,
):
    try:
        deleted = delete_conversation(
            request.state.user_id,
            conversation_id,
        )
    except DATABASE_ERRORS:
        raise HTTPException(503, "Conversation storage is unavailable.")

    if not deleted:
        raise HTTPException(404, "Conversation not found.")

    return {"ok": True}


# -------------------------------------------------------------------
# Authentication
# -------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path

    if path.startswith("/api/") and path not in (
        "/api/health",
        "/api/auth/login",
        "/api/auth/signup",
    ):
        try:
            user_id = session_user_id(
                request.cookies.get(SESSION_COOKIE)
            )
        except AuthConfigurationError as exc:
            return JSONResponse(
                {"detail": str(exc)},
                status_code=503,
            )

        if not user_id:
            return JSONResponse(
                {"detail": "Please sign in to continue."},
                status_code=401,
            )

        request.state.user_id = user_id

    return await call_next(request)


def set_session(response: Response, user_id: int):
    response.set_cookie(
        SESSION_COOKIE,
        create_session(user_id),
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=bool(os.environ.get("VERCEL")),
        path="/",
    )


@app.post("/api/auth/signup", status_code=201)
async def signup(credentials: LoginRequest, response: Response):
    try:
        session_secret()
        user_id, username = register(
            credentials.username,
            credentials.password,
        )
        set_session(response, user_id)

    except AuthConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except UsernameTaken:
        raise HTTPException(409, "This username is already taken.")
    except DATABASE_ERRORS:
        raise HTTPException(
            503,
            "Account storage is temporarily unavailable.",
        )

    return {"username": username}


@app.post("/api/auth/login")
async def login(credentials: LoginRequest, response: Response):
    try:
        session_secret()
        user = find_user(credentials.username)

    except AuthConfigurationError as exc:
        raise HTTPException(503, str(exc))
    except DATABASE_ERRORS:
        raise HTTPException(
            503,
            "Account storage is temporarily unavailable.",
        )

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
        raise HTTPException(
            503,
            "Account storage is temporarily unavailable.",
        )

    if user is None:
        raise HTTPException(401, "Please sign in to continue.")

    return {"username": user[1]}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


# -------------------------------------------------------------------
# Request models
# -------------------------------------------------------------------

class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_CHARS,
    )
    conversation_id: int | None = Field(default=None, gt=0)

    # Retained for compatibility with older frontend requests.
    # The database is now the source of truth for conversation history.
    history: List[HistoryMessage] = Field(
        default_factory=list,
        max_length=100,
    )


class GrammarRequest(BaseModel):
    text: str = Field(min_length=1, max_length=48000)
    task: Literal[
        "grammar",
        "formal",
        "simple",
        "concise",
        "reformat",
    ] = "grammar"


class SummarizeRequest(BaseModel):
    text: str = Field(min_length=1)
    length: Literal["short", "medium", "detailed"] = "medium"


# -------------------------------------------------------------------
# Model error handling
# -------------------------------------------------------------------

async def call_model_errors(coro):
    """Turn model configuration and transport failures into readable HTTP errors."""

    try:
        return await coro

    except ModelConfigurationError as exc:
        raise HTTPException(503, detail=str(exc))

    except ModelResponseError as exc:
        raise HTTPException(502, detail=str(exc))

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
        raise HTTPException(
            status_code=503,
            detail="The model server connection failed.",
        )

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"The model server returned an error "
                f"({exc.response.status_code}). "
                "Check its model name and credentials."
            ),
        )


# -------------------------------------------------------------------
# Health check
# -------------------------------------------------------------------

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
        "configuration_error": config_error,
        "account_storage": "reachable" if storage_ok else "unavailable",
        "login_configured": bool(
            storage_ok
            and (
                os.environ.get("LOCALMIND_SESSION_SECRET")
                or not os.environ.get("VERCEL")
            )
        ),
    }


# -------------------------------------------------------------------
# Database-backed chat helpers
# -------------------------------------------------------------------

def prepare_chat(user_id: int, request: ChatRequest):
    """
    Find or create a conversation and retrieve its previous messages.

    The conversation must belong to the logged-in user.
    """

    if request.conversation_id is None:
        conversation_id = create_conversation(
            user_id=user_id,
            title=request.message.strip()[:200] or "New chat",
            conversation_type="chat",
        )

        previous_messages = []

    else:
        conversation_id = request.conversation_id

        previous_messages = get_messages(
            user_id,
            conversation_id,
        )

        if previous_messages is None:
            raise HTTPException(404, "Conversation not found.")

    return conversation_id, previous_messages


def build_messages(request: ChatRequest, previous_messages=None):
    """
    Build the model prompt using messages retrieved from the database.
    """

    if previous_messages is None:
        previous_messages = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in request.history
        ]

    history = [
        {
            "role": message["role"],
            "content": message["content"][:MAX_MESSAGE_CHARS],
        }
        for message in previous_messages[-MAX_HISTORY_MESSAGES:]
    ]

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {
            "role": "user",
            "content": request.message.strip(),
        },
    ]


def persist_user_message(user_id: int, request: ChatRequest):
    """
    Create/find the conversation and save the incoming user message.
    """

    try:
        conversation_id, previous_messages = prepare_chat(
            user_id,
            request,
        )

        save_message(
            conversation_id,
            "user",
            request.message.strip(),
        )

    except HTTPException:
        raise

    except DATABASE_ERRORS:
        raise HTTPException(
            503,
            "Conversation storage is unavailable.",
        )

    messages = build_messages(
        request,
        previous_messages,
    )

    return conversation_id, messages


# -------------------------------------------------------------------
# General AI chat
# -------------------------------------------------------------------

@app.post("/api/chat")
async def chat(request: ChatRequest, http: Request):
    """Non-streaming chat with database persistence."""

    if not request.message.strip():
        raise HTTPException(422, "Message cannot be blank.")

    conversation_id, messages = persist_user_message(
        http.state.user_id,
        request,
    )

    reply = await call_model_errors(ask(messages))

    try:
        save_message(
            conversation_id,
            "assistant",
            reply,
        )

    except DATABASE_ERRORS:
        raise HTTPException(
            503,
            "The response was generated, but could not be saved.",
        )

    return {"reply": reply}


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest, http: Request):
    """Stream model responses and save completed conversations."""

    if not request.message.strip():
        raise HTTPException(422, "Message cannot be blank.")

    conversation_id, messages = persist_user_message(
        http.state.user_id,
        request,
    )

    client, response = await call_model_errors(
        open_stream(messages)
    )

    async def pieces():
        finished = False
        generated_parts = []

        try:
            async for line in response.aiter_lines():
                if not line:
                    continue

                try:
                    text, done = stream_content(line)

                except (ValueError, KeyError, IndexError):
                    continue

                if text:
                    generated_parts.append(text)
                    yield text

                if done:
                    finished = True
                    break

            if finished:
                generated_reply = "".join(generated_parts)

                try:
                    save_message(
                        conversation_id,
                        "assistant",
                        generated_reply,
                    )

                except DATABASE_ERRORS:
                    yield (
                        "\n\n[Your response was generated "
                        "but could not be saved.]"
                    )

            else:
                yield "\n\n[The model stream ended unexpectedly.]"

        except httpx.HTTPError:
            yield "\n\n[The connection to the model was lost.]"

        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        pieces(),
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Conversation-Id": str(conversation_id),
        },
    )


# -------------------------------------------------------------------
# Summarization
# -------------------------------------------------------------------

@app.post("/api/summarize")
async def summarize(request: SummarizeRequest):
    if not request.text.strip():
        raise HTTPException(422, "Text cannot be blank.")

    return await call_model_errors(
        summarize_document(
            request.text,
            request.length,
        )
    )


@app.post("/api/summarize/file")
async def summarize_file(
    file: UploadFile = File(...),
    length: Literal["short", "medium", "detailed"] = Form("medium"),
):
    data = await file.read(MAX_UPLOAD_BYTES + 1)

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File is too large. The limit is 5 MB.",
        )

    text = extract_text(
        file.filename or "",
        data,
    )

    result = await call_model_errors(
        summarize_document(text, length)
    )

    return {
        **result,
        "filename": file.filename,
        "characters": len(text),
    }


# -------------------------------------------------------------------
# Grammar improvement
# -------------------------------------------------------------------

@app.post("/api/improve-text")
async def improve(request: GrammarRequest):
    if not request.text.strip():
        raise HTTPException(422, "Text cannot be blank.")

    improved = await call_model_errors(
        improve_text(
            request.text,
            request.task,
        )
    )

    return {"improved_text": improved}


# -------------------------------------------------------------------
# Frontend
# -------------------------------------------------------------------

if FRONTEND_DIST.exists():
    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_DIST, html=True),
        name="frontend",
    )

else:

    @app.get("/")
    def home():
        return {
            "message": (
                "LocalMind AI backend is running. "
                "Build the frontend with 'npm run build' "
                "in /frontend to see the chat UI."
            )
        }
