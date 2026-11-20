# Running the HR Policy Navigator with Docker

One image (`deploy/Dockerfile`, Python 3.12) runs two services from `docker-compose.yml`:

| Service | URL | What it is |
|---|---|---|
| `api` | http://localhost:8000 | FastAPI: `/health`, `POST /chat`, `GET /chat/status`, `/hrms/...` |
| `ui` | http://localhost:8501 | Streamlit chat page (`app/chat_app.py`); runs the agent in its own process |

## Steps from a fresh clone

1. Install Docker Desktop and start it.
2. Copy `.env.example` to `.env` and fill in the chat and embeddings settings. Put the two CA files
   from the course zip into `certs/` and point to them as `CAPSTONE_CA_CERT=certs/<file>.crt` and
   `EMBED_CA_CERT=certs/<file>.crt`. Keys stay in `.env` only (it is gitignored and not copied into
   the image; compose passes it at run time).
3. Turn Zscaler off (it resets TLS to both course VMs).
4. From the repo root:

   ```bash
   docker compose up --build
   ```

5. Check:

   ```bash
   curl localhost:8000/health
   curl localhost:8000/chat/status
   curl -X POST localhost:8000/chat -H 'content-type: application/json' -d '{"question": "How long is probation at DEF?", "session_id": "s1", "employee_id": "D010"}'
   ```

   Then open http://localhost:8501, pick a persona and ask a policy question.

Optional, once, to turn on hybrid (keyword plus embedding) search; the vectors are cached in the
`app-cache` volume:

```bash
docker compose run --rm api python -m hr_navigator.retrieval.build --dense
```

## Without a .env

Both services start and pass their health checks. `/chat/status` works. `/chat` answers
"The assistant is not configured on this server: CHAT_BASE_URL is not set", and search runs in
keyword-only (BM25) mode.

## Checked on 20 Nov 2026 (Akash's laptop, no .env)

`docker compose build` and `up` work; both services healthy; `/health` and `/chat/status` answer;
the 11th `/chat` request in a minute from one session gets 429; the chat page loads. **Not yet
checked:** a real answer through Docker (needs `.env`, the CA files and the endpoints), and a run
from a fresh clone on another machine.
