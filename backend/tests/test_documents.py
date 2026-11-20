import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from docx import Document

import auth
import documents
import main


def make_docx(*paragraphs):
    buffer = io.BytesIO()
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(buffer)
    return buffer.getvalue()


class DocumentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.dict(os.environ, {
            "LOCALMIND_DB_PATH": str(Path(self.directory.name) / "users.db"),
            "LOCALMIND_SESSION_SECRET": "test-session-secret",
            "LLM_PROVIDER": "ollama",
        }, clear=True)
        settings.start()
        self.addCleanup(settings.stop)
        self.app = main.app
        self.transport = httpx.ASGITransport(app=self.app)

    def client(self):
        client = httpx.AsyncClient(transport=self.transport, base_url="http://test")
        self.addAsyncCleanup(client.aclose)
        return client

    async def signup(self, client, name):
        response = await client.post("/api/auth/signup", json={"username": name, "password": "a-long-password-123"})
        self.assertEqual(response.status_code, 201)

    async def test_documents_require_login(self):
        client = self.client()
        self.assertEqual((await client.post("/api/docs", files={"file": ("a.txt", b"hello")})).status_code, 401)

    async def test_upload_docx_and_owner_only_access(self):
        owner, other = self.client(), self.client()
        await self.signup(owner, "Owner")
        await self.signup(other, "Other")
        upload = await owner.post("/api/docs", files={"file": ("notes.docx", make_docx("The library closes at 8 pm."))})
        self.assertEqual(upload.status_code, 200)
        doc_id = upload.json()["doc_id"]
        # The document is reloaded from the database on each question.
        saved = documents.load_document(doc_id, auth.find_user("Owner")[0])
        self.assertIsNotNone(saved)
        self.assertIn("library closes", saved.chunks[0]["text"])
        # Another signed-in user cannot ask about someone else's document.
        denied = await other.post(f"/api/docs/{doc_id}/ask", json={"question": "When does it close?"})
        self.assertEqual(denied.status_code, 404)

    async def test_upload_rejects_bad_files(self):
        client = self.client()
        await self.signup(client, "Owner")
        cases = [("x.csv", b"a", 415), ("empty.txt", b"  ", 422), ("bad.txt", b"\xff", 422),
                 ("big.txt", b"x" * (documents.MAX_UPLOAD_BYTES + 1), 413)]
        for name, data, status in cases:
            response = await client.post("/api/docs", files={"file": (name, data)})
            self.assertEqual(response.status_code, status, name)

    def test_search_finds_the_matching_section(self):
        index = documents.DocIndex("a.txt", [(1, "Admissions close in January."), (2, "The library fine is 5 rupees per day.")])
        hits = index.search("What is the library fine?", k=1)
        self.assertEqual(index.chunks[hits[0]]["page"], 2)

    def test_export_docx_builds_a_valid_file(self):
        data = documents.build_docx("# Title\n- one\n- **two**", None)
        text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
        self.assertIn("Title", text)
        self.assertIn("two", text)


if __name__ == "__main__":
    unittest.main()
