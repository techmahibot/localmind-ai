
import httpx

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL_NAME = "qwen2.5:3b"


async def summarize_text(text: str, length: str = "medium"):

    prompts = {
        "short": "Summarize this text in 5 concise bullet points.",
        "medium": "Create a clear summary covering all important points.",
        "detailed": "Create a detailed, structured summary preserving important facts and technical details."
    }

    instruction = prompts.get(length, prompts["medium"])

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert document summarization assistant. "
                    "Preserve the original meaning, important facts, "
                    "numbers and technical details. "
                    "Never invent information."
                )
            },
            {
                "role": "user",
                "content": f"{instruction}\n\nText:\n{text}"
            }
        ],
        "stream": False
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(OLLAMA_URL, json=payload)
        response.raise_for_status()

        result = response.json()

    return result["message"]["content"]
