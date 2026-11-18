"""Model transport shared by chat, summarization, and writing tools.

Local Ollama is the default. A hosted OpenAI-compatible endpoint can be used
for an internet demo without changing the offline/LAN deployment path.
"""

import json
import os

import httpx


class ModelConfigurationError(Exception):
    pass


class ModelResponseError(Exception):
    pass


def provider():
    value = os.getenv("LLM_PROVIDER", "ollama").lower()
    if value not in ("ollama", "openai"):
        raise ModelConfigurationError("LLM_PROVIDER must be 'ollama' or 'openai'.")
    return value


def model_name():
    return os.getenv("LLM_MODEL", "qwen2.5:3b")


def configuration():
    kind = provider()
    if kind == "ollama":
        base = os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        if os.getenv("VERCEL") and base in ("http://127.0.0.1:11434", "http://localhost:11434"):
            raise ModelConfigurationError(
                "This Vercel deployment has no model server. Configure a hosted model "
                "with LLM_PROVIDER=openai, LLM_BASE_URL, LLM_MODEL, and LLM_API_KEY."
            )
        return kind, base + "/api/chat", base + "/api/tags", {}

    base = os.getenv("LLM_BASE_URL", "").rstrip("/")
    key = os.getenv("LLM_API_KEY", "")
    if not base or not key or not os.getenv("LLM_MODEL"):
        raise ModelConfigurationError(
            "Configure LLM_BASE_URL, LLM_MODEL, and LLM_API_KEY for the hosted model."
        )
    if not base.startswith("https://"):
        raise ModelConfigurationError("Hosted model LLM_BASE_URL must use HTTPS.")
    return kind, base + "/chat/completions", base + "/models", {"Authorization": "Bearer " + key}


def payload(messages, stream=False, options=None):
    kind = provider()
    body = {"model": model_name(), "messages": messages, "stream": stream}
    if kind == "ollama" and options:
        body["options"] = options
    return body


def reply_content(data):
    try:
        if provider() == "ollama":
            content = data["message"]["content"]
        else:
            content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelResponseError("The model returned an unexpected response.") from exc
    if not isinstance(content, str):
        raise ModelResponseError("The model returned an unexpected response.")
    return content


def stream_content(line):
    if provider() == "ollama":
        data = json.loads(line)
        return data.get("message", {}).get("content", ""), data.get("done", False)
    if not line.startswith("data: "):
        return "", False
    content = line[6:]
    if content == "[DONE]":
        return "", True
    data = json.loads(content)
    return data.get("choices", [{}])[0].get("delta", {}).get("content") or "", False


async def ask(messages, options=None):
    _, url, _, headers = configuration()
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(url, headers=headers, json=payload(messages, options=options))
        response.raise_for_status()
        try:
            return reply_content(response.json())
        except ValueError as exc:
            raise ModelResponseError("The model returned invalid JSON.") from exc


async def open_stream(messages, options=None):
    _, url, _, headers = configuration()
    client = httpx.AsyncClient(timeout=180.0)
    try:
        response = await client.send(
            client.build_request("POST", url, headers=headers, json=payload(messages, stream=True, options=options)),
            stream=True,
        )
        response.raise_for_status()
        return client, response
    except BaseException:
        await client.aclose()
        raise


async def reachable():
    _, _, url, headers = configuration()
    async with httpx.AsyncClient(timeout=2.0) as client:
        response = await client.get(url, headers=headers)
        return response.status_code == 200
