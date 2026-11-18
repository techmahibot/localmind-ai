import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

import llm


class ModelTransportTests(unittest.TestCase):
    def test_local_ollama_response_and_stream(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True):
            kind, url, _, headers = llm.configuration()
            self.assertEqual((kind, url, headers), ("ollama", "http://127.0.0.1:11434/api/chat", {}))
            self.assertEqual(llm.reply_content({"message": {"content": "Hello"}}), "Hello")
            self.assertEqual(llm.stream_content('{"message":{"content":"Hi"},"done":false}'), ("Hi", False))
            with self.assertRaises(llm.ModelResponseError):
                llm.reply_content({"message": {"content": None}})

    def test_malformed_hosted_reply_is_reported(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True):
            with self.assertRaises(llm.ModelResponseError):
                llm.reply_content({"choices": []})

    def test_vercel_requires_reachable_model(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with self.assertRaises(llm.ModelConfigurationError):
                llm.configuration()

    def test_hosted_provider_requires_configuration_and_https(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True):
            with self.assertRaises(llm.ModelConfigurationError):
                llm.configuration()
        settings = {"LLM_PROVIDER": "openai", "LLM_BASE_URL": "http://example.com/v1",
                    "LLM_MODEL": "demo", "LLM_API_KEY": "test-key"}
        with patch.dict(os.environ, settings, clear=True):
            with self.assertRaises(llm.ModelConfigurationError):
                llm.configuration()

    def test_hosted_chat_and_stream(self):
        settings = {"LLM_PROVIDER": "openai", "LLM_BASE_URL": "https://example.com/v1",
                    "LLM_MODEL": "demo", "LLM_API_KEY": "test-key"}
        seen = []

        def handler(request):
            seen.append(request)
            if request.url.path == "/v1/chat/completions":
                if json.loads(request.content)["stream"]:
                    return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Streamed"}}]}\n\ndata: [DONE]\n\n')
                return httpx.Response(200, json={"choices": [{"message": {"content": "A response"}}]})
            return httpx.Response(404)

        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(handler)

        def mock_client(*args, **kwargs):
            return original_client(transport=transport, **kwargs)

        with patch.dict(os.environ, settings, clear=True), patch.object(llm.httpx, "AsyncClient", mock_client):
            result = asyncio.run(llm.ask([{"role": "user", "content": "Hi"}]))
            self.assertEqual(result, "A response")
            self.assertEqual(seen[0].headers["Authorization"], "Bearer test-key")
            self.assertEqual(llm.stream_content('data: {"choices":[{"delta":{"content":"Hello"}}]}'), ("Hello", False))
            self.assertEqual(llm.stream_content("data: [DONE]"), ("", True))

            async def read_stream():
                client, response = await llm.open_stream([{"role": "user", "content": "Hi"}])
                try:
                    return [llm.stream_content(line) async for line in response.aiter_lines() if line]
                finally:
                    await response.aclose()
                    await client.aclose()

            self.assertEqual(asyncio.run(read_stream()), [("Streamed", False), ("", True)])
            self.assertEqual(seen[1].headers["Authorization"], "Bearer test-key")


if __name__ == "__main__":
    unittest.main()
