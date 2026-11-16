
import httpx

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL_NAME = "qwen2.5:3b"


async def improve_text(text: str, task: str = "grammar"):
    prompts = {
        "grammar": (
            "Correct grammar, spelling, and punctuation errors. "
            "Preserve the original meaning and writing style. "
            "Do not add new information."
        ),
        "formal": (
            "Rewrite the text in a clear, professional, and formal tone. "
            "Preserve the original meaning and all important details."
        ),
        "simple": (
            "Rewrite the text using simple, clear, and easy-to-understand "
            "language. Preserve the original meaning."
        ),
        "concise": (
            "Make the text shorter and more concise while preserving "
            "all important information."
        ),
        "reformat": (
            "Improve the structure and readability of the text. "
            "Organize related ideas into paragraphs and use bullet points "
            "where appropriate. Preserve the original meaning."
        )
    }

    instruction = prompts.get(task)

    if instruction is None:
        raise ValueError(
            "Invalid task. Choose grammar, formal, simple, concise, or reformat."
        )

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an expert writing assistant. "
                    "Improve the user's text according to the requested task. "
                    "Return only the improved text, without explanations. "
                    "Never change the intended meaning or invent information."
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
