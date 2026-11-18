# LocalMind AI API contract

Base path: `/api`. The React frontend and API should use the same origin. All routes except health, sign-up, and login require the `localmind_session` cookie. Browsers send this cookie automatically on same-origin requests. Do not store the password or session cookie in JavaScript storage.

## Accounts

| Method and route | JSON request | Success | Common errors |
|---|---|---|---|
| `POST /auth/signup` | `{"username":"Mahi","password":"a-long-password-123"}` | 201, `{"username":"Mahi"}`; sets session cookie | 400 invalid username/password, 409 taken, 503 storage/secret unavailable |
| `POST /auth/login` | Same fields as sign-up | 200, `{"username":"Mahi"}`; sets session cookie | 401 wrong credentials, 503 storage/secret unavailable |
| `GET /auth/me` | None | 200, `{"username":"Mahi"}` | 401 no/expired session, 503 storage unavailable |
| `POST /auth/logout` | None | 200, `{"ok":true}`; clears cookie | 401 no/expired session |

Usernames are 3–30 ASCII letters, numbers, or underscores and are matched without case sensitivity. Passwords must be 10–128 characters. Sessions last 12 hours.

## Chat

`POST /chat` accepts:

```json
{
  "message": "Explain local networks",
  "history": [
    {"role": "user", "content": "What is a LAN?"},
    {"role": "assistant", "content": "A local area network connects nearby devices."}
  ]
}
```

Success is `{"reply":"A local network..."}`. `history` is optional; the browser sends prior turns, while the backend uses the most recent 20. `message` has a 12,000-character limit and cannot be blank. Each history entry must have `role` equal to `user` or `assistant`.

`POST /chat/stream` accepts the same JSON and returns `text/plain` chunks, **not JSON or server-sent events**. Read `response.body` as a stream and append decoded chunks. If the model disconnects after headers have been sent, the stream ends with a bracketed error message. Stop in the browser with an `AbortController`.

## Summarization

`POST /summarize` accepts `{"text":"Your source text","length":"medium"}`. Length is `short`, `medium`, or `detailed`, defaulting to `medium`.

`POST /summarize/file` accepts `multipart/form-data`: field `file` contains a UTF-8 `.txt`/`.md` or text-based `.pdf`; optional field `length` has the same values. Upload limit: 5 MB. Scanned image-only PDFs need OCR and are currently rejected.

Both return `{"summary":"...","chunks":1,"truncated":false}`. The file endpoint also returns `filename` and `characters`. When `truncated` is true, only the first eight document chunks were summarized; the UI must show this warning.

## Writing tools

`POST /improve-text` accepts `{"text":"i has a question","task":"grammar"}`. Allowed tasks: `grammar`, `formal`, `simple`, `concise`, `reformat`. The default is `grammar`; text cannot be blank and is limited to 48,000 characters. Success is `{"improved_text":"I have a question."}`.

## Health and errors

`GET /health` is public. It reports `model_server` as `reachable` or `unavailable`, `account_storage` as `reachable` or `unavailable`, `login_configured`, `model`, and `configuration_error`. This checks connections; it does not generate a model reply.

Failed JSON requests use FastAPI's `{"detail":"..."}` format, or a validation detail array for 422 responses. Common statuses: 401 missing/expired session, 413 file too large, 415 unsupported extension, 422 invalid input or unreadable file, 502 bad upstream response, 503 model/storage unavailable, 504 model timeout. The frontend should display a readable message for both string and validation-array details.

## Run checks locally

Install `backend/requirements.txt` in a virtual environment, then from the repository root run:

```bash
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
```

The tests mock model replies and use temporary local account storage. Demonstrate Ollama, multi-device LAN access, and disconnected operation separately on the host computer.
