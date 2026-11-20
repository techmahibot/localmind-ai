# Disconnected LAN demonstration

Use **one host computer** for Ollama, FastAPI, and the built frontend, and **one second device** as the browser/API client. A Vercel deployment cannot prove this requirement.

## Prepare while internet is available

1. On the host, install the backend requirements, frontend dependencies, Ollama, and `qwen2.5:3b`. Run `ollama list` to confirm the model is downloaded.
2. Build the frontend: `cd frontend && npm ci && npm run build`.
3. Set a stable `LOCALMIND_SESSION_SECRET` on the host. Leave `DATABASE_URL` unset so account storage uses local SQLite, and leave `LLM_PROVIDER` unset so the model uses local Ollama.
4. From `backend/`, start `uvicorn main:app --host 0.0.0.0 --port 8000`. Ensure Ollama is running on the same host.
5. Find the host's LAN IP address. On macOS Wi-Fi, `ipconfig getifaddr en0` often shows it; check the active network interface if that command returns nothing. On the second device, open `http://HOST_IP:8000`, create a test account, then sign out and sign in again.

## Disconnect internet, keep the LAN

Disconnect the **router's WAN/internet connection** while both devices remain connected to the same Wi-Fi/LAN. Turning off Wi-Fi on either device would also break the LAN and invalidate the test. Confirm an external website cannot load on both devices.

From the second device, use the UI to check chat, a follow-up question, streaming, text summary, a small text/PDF upload, and grammar correction. A single host terminal can verify local model response, but does not prove second-device access.

Optionally, if Python 3 is installed on the second device, run:

```bash
python3 scripts/lan_smoke.py http://HOST_IP:8000 --username YOUR_TEST_USER --output lan-result.json
```

The script prompts for the password without including it in the command or report. It checks the frontend, health, login/session, chat, streaming, summary, and grammar routes. It does **not** determine whether the WAN is disconnected or whether it ran on a second device; record those two facts yourself. Use `--signup` if the test account does not exist yet.

## Evidence for the evaluator

Record the date, host and client device names, LAN IP, Ollama model, screenshots of the UI on the second device, the router/internet state, and the smoke-check JSON. Do not publish real passwords, session cookies, database URLs, or hosted API keys. Include response times if you compare models or multiple clients.

The test is complete only when the app works from the second device while the router has no internet connection. Reconnect the WAN afterward.
