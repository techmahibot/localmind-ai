import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import grammer
import summarizer


class DocumentToolTests(unittest.TestCase):
    def test_split_text_preserves_content_at_chunk_boundary(self):
        original = "one\n" + "a" * 6001 + "\nlast"
        chunks = summarizer.split_text(original)
        self.assertTrue(all(len(chunk) <= summarizer.CHUNK_CHARS for chunk in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), original.replace("\n", ""))

    def test_long_summary_reports_truncation(self):
        with patch.object(summarizer, "_ask", new_callable=AsyncMock, return_value="A summary") as mock:
            result = asyncio.run(summarizer.summarize_document("x" * (summarizer.CHUNK_CHARS * 9)))
        self.assertTrue(result["truncated"])
        self.assertEqual(result["chunks"], summarizer.MAX_CHUNKS)
        self.assertEqual(mock.await_count, summarizer.MAX_CHUNKS + 1)

    def test_writing_modes_and_invalid_task(self):
        with patch.object(grammer, "ask", new_callable=AsyncMock, return_value="Improved.") as mock:
            for task in ("grammar", "formal", "simple", "concise", "reformat"):
                self.assertEqual(asyncio.run(grammer.improve_text("Original text.", task)), "Improved.")
            self.assertEqual(mock.await_count, 5)
            with self.assertRaises(ValueError):
                asyncio.run(grammer.improve_text("Original text.", "unknown"))


if __name__ == "__main__":
    unittest.main()
