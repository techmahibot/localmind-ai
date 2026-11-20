from llm import ask

# A 3B model only "sees" a few thousand tokens at once, so long documents are
# split into sections, each section is summarized, and then the partial
# summaries are combined into one final summary.
CHUNK_CHARS = 6000
MAX_CHUNKS = 8
NUM_CTX = 4096

PROMPTS = {
    "short": "Summarize this text in 5 concise bullet points.",
    "medium": "Create a clear summary covering all important points.",
    "detailed": "Create a detailed, structured summary preserving important facts and technical details.",
}

MODE_PROMPTS = {
    "general": "",
    "science": (
        "For a science or technology document, preserve the research question, methods, "
        "technical terms, measured results, units, limitations and conclusions. "
        "Distinguish the author's claims from demonstrated results."
    ),
    "news": (
        "For supplied newspaper headlines or editorials, group related developments, "
        "include dates and sources only when present, and distinguish reported facts "
        "from opinions or predictions. Do not imply access to current news."
    ),
}

SYSTEM_PROMPT = (
    "You are an expert document summarization assistant. "
    "Preserve the original meaning, important facts, "
    "numbers and technical details. "
    "Never invent information."
)


def split_text(text: str, size: int = CHUNK_CHARS):
    """Split text into pieces of at most `size` characters, preferring line breaks."""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > size:  # a single huge line: cut it
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if current and len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def _ask(instruction: str, text: str, model: str = "") -> str:
    return await ask(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{instruction}\n\nText:\n{text}"},
        ],
        options={"num_ctx": NUM_CTX},
        selected_model=model or None,
    )


async def summarize_document(
    text: str, length: str = "medium", mode: str = "general", topic: str = "",
    model: str = ""
) -> dict:
    """Summarize text of any length. Returns summary, number of sections and a truncated flag."""
    if mode not in MODE_PROMPTS:
        raise ValueError("Invalid summary mode.")
    instruction = PROMPTS.get(length, PROMPTS["medium"])
    if MODE_PROMPTS[mode]:
        instruction += " " + MODE_PROMPTS[mode]
    focus = f" Focus the overview on this topic: {topic.strip()[:120]}." if mode == "news" and topic.strip() else ""
    instruction += focus
    chunks = split_text(text)
    truncated = len(chunks) > MAX_CHUNKS
    chunks = chunks[:MAX_CHUNKS]

    if len(chunks) == 1:
        summary = await _ask(instruction, chunks[0], model)
    else:
        partials = []
        for i, chunk in enumerate(chunks, start=1):
            partials.append(
                await _ask(
                    f"This is part {i} of {len(chunks)} of a longer document. "
                    "Summarize it in at most 150 words, keeping key facts and numbers. "
                    + MODE_PROMPTS[mode] + focus,
                    chunk,
                    model,
                )
            )
        summary = await _ask(instruction, "\n\n".join(partials), model)

    return {"summary": summary, "chunks": len(chunks), "truncated": truncated}


async def summarize_text(text: str, length: str = "medium") -> str:
    """Kept for backward compatibility: returns only the summary string."""
    return (await summarize_document(text, length))["summary"]
