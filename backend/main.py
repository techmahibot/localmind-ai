
from fastapi import FastAPI
from pydantic import BaseModel
import httpx

app = FastAPI(title="LocalMind AI")

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL_NAME = "qwen2.5:3b"


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def home():
    return {"message": "Welcome to LocalMind AI!"}


@app.get("/api/health")
def health():
    return {"status": "LocalMind AI backend is running"}


@app.post("/api/chat")
async def chat(request: ChatRequest):

    payload = {
        "model": MODEL_NAME,

"messages": [
    {
        "role": "system",
        "content": (
            "You are LocalMind AI, an offline AI assistant. "
            "Answer questions clearly and naturally. "
            "Do not claim to access the internet or live information. "
            "Be honest when you do not know something."
        )
    },
    {
        "role": "user",
        "content": request.message
    }
],
        "stream": False
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        result = response.json()

    return {
        "reply": result["message"]["content"]
    }
