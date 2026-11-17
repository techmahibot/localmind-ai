
from llm import ask


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

    return await ask(
        [
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
        ]
    )
