
"""Document features for LocalMind AI.

- Reading uploaded files (PDF, Word .docx, .txt, .md)
- "Ask your documents": question answering over an uploaded file, with page sources
- Improving long text section by section
- Exporting text as a Word (.docx) file

Everything runs locally. Retrieval uses BM25 keyword scoring, so no extra AI model is needed.
"""

import asyncio
import base64
import io
import json
import math
import os
import re
import socket
import time
import uuid
from collections import Counter
from typing import List, Literal, Optional

import httpx
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from pypdf import PdfReader

from auth import AuthConfigurationError, DATABASE_ERRORS, connection
from grammer import improve_text
from chat_storage import (
    create_conversation,
    get_messages,
    list_conversations,
    save_message,
)
from llm import ModelConfigurationError, ModelResponseError, open_stream, resolve_model, stream_content
from summarizer import split_text

NUM_CTX = 4096

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
CHUNK_CHARS = 900  # size of one searchable section of a document
TOP_K = 4  # how many sections are sent to the model with each question
MAX_DOCS = 30  # documents kept per account; older documents are pruned
MAX_HISTORY = 6  # earlier chat messages sent along for follow-up questions

IMPROVE_CHUNK_CHARS = 2500
MAX_IMPROVE_CHARS = 15000

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------------
# Model errors
# ----------------------------------------------------------------------------


async def model_errors(coro):
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
            detail="Cannot reach the model server. Check that Ollama is running and the model is pulled.",
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="The model took too long to respond. Try a shorter input.",
        )
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="The model server connection failed.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The model server returned an error ({exc.response.status_code}). Check its model name and credentials.",
        )


# ----------------------------------------------------------------------------
# Reading uploaded files
# ----------------------------------------------------------------------------


def _docx_text(data: bytes) -> str:
    """Text of a Word file in reading order: paragraphs and table rows."""
    document = Document(io.BytesIO(data))
    parts = []

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            parts.append(Paragraph(child, document).text)
        elif child.tag.endswith("}tbl"):
            for row in Table(child, document).rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))

    return "\n".join(parts)


def extract_pages(filename: str, data: bytes):
    """Return [(page_number_or_None, text), ...] for supported document formats."""
    name = filename.lower()

    if name.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [
                (number, (page.extract_text() or "").strip())
                for number, page in enumerate(reader.pages, start=1)
            ]
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="Could not read that PDF. It may be damaged or password protected.",
            )

    elif name.endswith(".docx"):
        try:
            pages = [(None, _docx_text(data).strip())]
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="Could not read that Word file. Make sure it is a .docx file (not the older .doc).",
            )

    elif name.endswith((".txt", ".md")):
        try:
            pages = [(None, data.decode("utf-8").strip())]
        except UnicodeDecodeError:
            raise HTTPException(422, "Text files must be UTF-8 encoded.")

    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Please upload a .pdf, .docx, .txt or .md file.",
        )

    pages = [(number, text) for number, text in pages if text]

    if not pages:
        raise HTTPException(
            status_code=422,
            detail="No readable text found in that file. Scanned PDFs (images of text) are not supported.",
        )

    return pages


def extract_text(filename: str, data: bytes) -> str:
    """All the text of an uploaded file as one string."""
    return "\n".join(text for _, text in extract_pages(filename, data))


async def read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large. The limit is 5 MB.")

    return data


# ----------------------------------------------------------------------------
# Searching a document (BM25 keyword scoring)
# ----------------------------------------------------------------------------


STOPWORDS = set(
    "a an and are as at be but by for from has have he her his how i if in into is it its of on or "
    "she that the their them then there these they this to was we were what when where which who why "
    "will with you your do does did can could should would about not no so than too very".split()
)

SPLIT = re.compile(r"[\s.,;:!?()\[\]{}\"'“”‘’\-–—/\\|*_#<>=+~`^]+")

OVERVIEW = re.compile(
    r"\b(summar\w*|overview|main points?|key points?|key takeaways?|tl;?dr|"
    r"what is this (document|file|paper|pdf|report)|about this)\b",
    re.IGNORECASE,
)

K1, B = 1.5, 0.75


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]

    return word


def tokenize(text: str) -> List[str]:
    return [
        _stem(word)
        for word in SPLIT.split(text.lower())
        if len(word) > 1 and word not in STOPWORDS
    ]


def chunk_text(text: str, size: int = CHUNK_CHARS) -> List[str]:
    """Cut text into sections of at most size characters, keeping lines together."""
    pieces, current = [], ""

    for line in text.split("\n"):
        line = line.strip()

        if not line:
            continue

        while len(line) > size:
            cut = line.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size

            if current:
                pieces.append(current)
                current = ""

            pieces.append(line[:cut])
            line = line[cut:].strip()

        if current and len(current) + len(line) + 1 > size:
            pieces.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        pieces.append(current)

    return pieces


class DocIndex:
    def __init__(self, filename: str, pages, owner=None):
        self.filename = filename
        self.owner = owner
        self.created = time.time()
        self.chunks = []

        for page, text in pages:
            for piece in chunk_text(text):
                self.chunks.append({"text": piece, "page": page})

        self.tokens = [Counter(tokenize(c["text"])) for c in self.chunks]
        self.lengths = [sum(c.values()) for c in self.tokens]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) or 1

        self.df = Counter()

        for counts in self.tokens:
            self.df.update(counts.keys())

    def _spread(self, k: int) -> List[int]:
        """Sections spread evenly across the whole document."""
        n = len(self.chunks)

        if n <= k:
            return list(range(n))

        if k <= 1:
            return [0]

        return sorted({round(j * (n - 1) / (k - 1)) for j in range(k)})

    def search(self, query: str, k: int = TOP_K) -> List[int]:
        """Indexes of the best matching sections, in reading order."""
        if OVERVIEW.search(query):
            return self._spread(k)

        terms = set(tokenize(query))
        n = len(self.chunks)
        scored = []

        for i, counts in enumerate(self.tokens):
            score = 0.0

            for term in terms:
                f = counts.get(term, 0)

                if not f:
                    continue

                df = self.df[term]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                norm = f + K1 * (1 - B + B * self.lengths[i] / self.avg_len)

                score += idf * f * (K1 + 1) / norm

            if score > 0:
                scored.append((score, i))

        if not scored:
            return self._spread(k)

        scored.sort(reverse=True)

        return sorted(i for _, i in scored[:k])


def ensure_documents_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS localmind_documents ("
        "id TEXT PRIMARY KEY, owner_id BIGINT NOT NULL, filename TEXT NOT NULL, "
        "pages_json TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL)"
    )


def store_document(index: DocIndex, pages) -> str:
    serialized = json.dumps(pages, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The extracted text is too large. The limit is 5 MB.")
    doc_id = uuid.uuid4().hex
    with connection() as (conn, postgres):
        ensure_documents_table(conn)
        marker = "%s" if postgres else "?"
        conn.execute(
            f"INSERT INTO localmind_documents (id, owner_id, filename, pages_json, created_at) "
            f"VALUES ({', '.join([marker] * 5)})",
            (doc_id, index.owner, index.filename, serialized, time.time()),
        )
        rows = conn.execute(
            f"SELECT id FROM localmind_documents WHERE owner_id = {marker} "
            "ORDER BY created_at DESC, id DESC",
            (index.owner,),
        ).fetchall()
        for (old_id,) in rows[MAX_DOCS:]:
            conn.execute(f"DELETE FROM localmind_documents WHERE id = {marker}", (old_id,))
    return doc_id


def load_document(doc_id: str, owner_id: int):
    with connection() as (conn, postgres):
        ensure_documents_table(conn)
        marker = "%s" if postgres else "?"
        row = conn.execute(
            f"SELECT filename, pages_json FROM localmind_documents "
            f"WHERE id = {marker} AND owner_id = {marker}",
            (doc_id, owner_id),
        ).fetchone()
    if row is None:
        return None
    return DocIndex(row[0], json.loads(row[1]), owner_id)


# ----------------------------------------------------------------------------
# Document conversation models
# ----------------------------------------------------------------------------


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    history: List[ChatTurn] = Field(default_factory=list)
    conversation_id: Optional[int] = None
    model: str = Field(default="", max_length=120)


DOC_SYSTEM_PROMPT = (
    "You answer questions using ONLY the document excerpts provided by the user. "
    "If the answer is not in the excerpts, say that you could not find it in the document. "
    "Cite the excerpts you used with their numbers, like [1] or [2]. "
    "Do not invent information."
)


# ----------------------------------------------------------------------------
# Upload document
# ----------------------------------------------------------------------------


@router.post("/docs")
async def upload_document(request: Request, file: UploadFile = File(...)):
    data = await read_upload(file)
    name = file.filename or "document"

    pages = await asyncio.to_thread(extract_pages, name, data)
    index = await asyncio.to_thread(DocIndex, name, pages, getattr(request.state, "user_id", None))
    try:
        doc_id = await asyncio.to_thread(store_document, index, pages)
    except (AuthConfigurationError, *DATABASE_ERRORS):
        raise HTTPException(503, "Document storage is temporarily unavailable.")
    page_numbers = [number for number, _ in pages if number is not None]

    return {
        "doc_id": doc_id,
        "filename": name,
        "characters": sum(len(text) for _, text in pages),
        "sections": len(index.chunks),
        "pages": len(page_numbers) or None,
    }


# ----------------------------------------------------------------------------
# Ask questions about a document (with database persistence)
# ----------------------------------------------------------------------------


@router.post("/docs/{doc_id}/ask")
async def ask_document(doc_id: str, request: AskRequest, http: Request):
    user_id = getattr(http.state, "user_id", None)
    try:
        index = await asyncio.to_thread(load_document, doc_id, user_id)
    except (AuthConfigurationError, *DATABASE_ERRORS):
        raise HTTPException(503, "Document storage is temporarily unavailable.")
    # Documents belong to the person who uploaded them; others get the same 404.
    if index is None or index.owner != user_id:
        raise HTTPException(
            status_code=404,
            detail="This document is no longer available on the server. Please upload it again.",
        )

    question = request.question.strip()

    if not question:
        raise HTTPException(422, "Question cannot be blank.")

    # Load the existing conversation or create a new one.
    try:
        if request.conversation_id is not None:
            # Only document conversations belonging to this user are considered.
            conversations = list_conversations(user_id, "document")

            matching = next(
                (
                    conversation
                    for conversation in conversations
                    if conversation["id"] == request.conversation_id
                    and conversation["document_name"] == index.filename
                ),
                None,
            )

            if matching is None:
                raise HTTPException(404, "Document conversation not found.")

            previous_messages = get_messages(
                user_id,
                request.conversation_id,
            )

            if previous_messages is None:
                raise HTTPException(404, "Document conversation not found.")

            conversation_id = request.conversation_id

            history = [
                {
                    "role": message["role"],
                    "content": message["content"],
                }
                for message in previous_messages
            ][-MAX_HISTORY:]

        else:
            # The first question creates a persistent document conversation.
            conversation_id = create_conversation(
                user_id=user_id,
                title=question[:80],
                conversation_type="document",
                document_name=index.filename,
            )

            history = []

        # Save the question before generating the answer.
        save_message(
            conversation_id,
            "user",
            question,
        )

    except HTTPException:
        raise
    except DATABASE_ERRORS:
        raise HTTPException(
            503,
            "Document conversation storage is unavailable.",
        )

    # Include the previous question when the current question is a short follow-up.
    query = question

    if len(question.split()) < 6:
        previous = next(
            (
                message["content"]
                for message in reversed(history)
                if message["role"] == "user"
            ),
            "",
        )

        if previous:
            query = f"{previous} {question}"

    hits = index.search(query)

    excerpts = []
    sources = []

    for number, i in enumerate(hits, start=1):
        chunk = index.chunks[i]
        where = f" (page {chunk['page']})" if chunk["page"] else ""

        excerpts.append(
            f"[{number}]{where}\n{chunk['text']}"
        )

        sources.append({
            "n": number,
            "page": chunk["page"],
            "snippet": chunk["text"][:200],
        })

    messages = [
        {"role": "system", "content": DOC_SYSTEM_PROMPT},
        *history,
        {
            "role": "user",
            "content": (
                f'Document excerpts from "{index.filename}":\n\n'
                + "\n\n".join(excerpts)
                + f"\n\nQuestion: {question}"
            ),
        },
    ]

    selected = await model_errors(resolve_model(request.model))
    client, response = await model_errors(
        open_stream(messages, options={"num_ctx": NUM_CTX}, selected_model=selected)
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
                        sources=sources,
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

    # Sources are sent in a header so any language is safe.
    encoded = base64.b64encode(
        json.dumps(sources, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")

    return StreamingResponse(
        pieces(),
        media_type="text/plain; charset=utf-8",
        headers={
            "X-Sources": encoded,
            "X-Conversation-Id": str(conversation_id),
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ----------------------------------------------------------------------------
# Improving long text
# ----------------------------------------------------------------------------


ImproveTask = Literal["grammar", "formal", "simple", "concise", "reformat"]


class ImproveRequest(BaseModel):
    text: str = Field(min_length=1)
    task: ImproveTask = "grammar"
    model: str = Field(default="", max_length=120)


async def improve_long(text: str, task: str, model: str = "") -> dict:
    """Improve text of any reasonable length, one section at a time."""
    text = text.strip()

    if len(text) > MAX_IMPROVE_CHARS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"This text is too long to improve in one go "
                f"({len(text):,} characters; the limit is {MAX_IMPROVE_CHARS:,}). "
                "Split it into smaller parts."
            ),
        )

    chunks = split_text(text, IMPROVE_CHUNK_CHARS)
    improved = []

    for chunk in chunks:
        improved.append((await improve_text(chunk, task, model)).strip())
    return {"improved_text": "\n".join(improved), "chunks": len(chunks)}


@router.post("/improve-text/long")
async def improve_pasted_text(request: ImproveRequest):
    if not request.text.strip():
        raise HTTPException(422, "Text cannot be blank.")

    selected = await model_errors(resolve_model(request.model))
    return await model_errors(improve_long(request.text, request.task, selected))


@router.post("/improve-text/file")
async def improve_file(
    file: UploadFile = File(...),
    task: ImproveTask = Form("grammar"),
    model: str = Form("", max_length=120),
):
    data = await read_upload(file)
    text = await asyncio.to_thread(extract_text, file.filename or "", data)
    selected = await model_errors(resolve_model(model))
    result = await model_errors(improve_long(text, task, selected))
    return {**result, "filename": file.filename, "characters": len(text)}


# ----------------------------------------------------------------------------
# Export as a Word file
# ----------------------------------------------------------------------------


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
BOLD = re.compile(r"(\*\*[^*]+\*\*)")


class ExportRequest(BaseModel):
    text: str = Field(min_length=1)
    title: Optional[str] = None


def _add_runs(paragraph, text: str):
    """Add text to a paragraph, turning **bold** markers into real bold."""
    for part in BOLD.split(text):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            paragraph.add_run(part[2:-2]).bold = True
        elif part:
            paragraph.add_run(part)


def build_docx(text: str, title: Optional[str]) -> bytes:
    document = Document()

    if title:
        document.add_heading(title, level=1)

    for raw in text.splitlines():
        line = raw.strip()

        if not line:
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        bullet = re.match(r"^[-*•]\s+(.*)", line)
        numbered = re.match(r"^\d+[.)]\s+(.*)", line)

        if heading:
            document.add_heading(
                heading.group(2).replace("**", ""),
                level=len(heading.group(1)) + 1,
            )
        elif bullet:
            _add_runs(
                document.add_paragraph(style="List Bullet"),
                bullet.group(1),
            )
        elif numbered:
            _add_runs(
                document.add_paragraph(style="List Number"),
                numbered.group(1),
            )
        else:
            _add_runs(document.add_paragraph(), line)

    buffer = io.BytesIO()
    document.save(buffer)

    return buffer.getvalue()


@router.post("/export/docx")
async def export_docx(request: ExportRequest):
    content = await asyncio.to_thread(
        build_docx,
        request.text,
        request.title,
    )

    return Response(
        content=content,
        media_type=DOCX_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="localmind-document.docx"'
        },
    )


# ----------------------------------------------------------------------------
# Network address
# ----------------------------------------------------------------------------


def lan_addresses() -> List[str]:
    """IPv4 addresses of this computer on the local network, the main one first."""
    found = []

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass

    usable = [
        ip for ip in found
        if not ip.startswith(("127.", "169.254.", "0."))
    ]

    return list(dict.fromkeys(usable))


@router.get("/network")
async def network(request: Request):
    if os.environ.get("VERCEL"):
        return {"urls": []}
    port = request.url.port or 80

    return {
        "urls": [f"http://{ip}:{port}" for ip in lan_addresses()]
    }
