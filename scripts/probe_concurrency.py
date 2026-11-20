"""Probe only the concurrency rule: what happens to a second request while the first is running?

    cd Code
    read -s API_KEY; export API_KEY      # key never goes in a file
    CHAT_BASE_URL=... CHAT_MODEL=... CAPSTONE_CA_CERT=... .venv/bin/python scripts/probe_concurrency.py

Zscaler must be off. Makes 4 small requests. Prints status codes, timings and the Retry-After
header only; never the key. Outcomes:
  second answered while first still running  -> no limit at this scale (or a queue that is fast)
  second status 429                          -> rejected: the client's retry/lock is needed (it exists)
  second slow, finished after the first      -> queued by the server
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from hr_navigator.llm import config, http  # noqa: E402


def post(client, body, label, t0):
    start = time.perf_counter()
    try:
        r = client.post("/chat/completions", json=body)
    except httpx.HTTPError as exc:
        return {"label": label, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}
    return {"label": label, "status": r.status_code, "started_s": round(start - t0, 2),
            "took_s": round(time.perf_counter() - start, 2), "retry_after": r.headers.get("retry-after"),
            "body_start": "" if r.status_code == 200 else r.text[:150]}


def main() -> None:
    ep = config.chat_endpoint()
    a, b = http.make_client(ep), http.make_client(ep)
    off = {"chat_template_kwargs": {"enable_thinking": False}}
    base = {"model": ep.model, "temperature": 0.3, **off}
    quick = {**base, "max_tokens": 20, "messages": [{"role": "user", "content": "Reply with the word: pong"}]}
    slow = {**base, "max_tokens": 400, "messages": [{"role": "user", "content": "Write 250 words about annual leave."}]}
    report: dict = {"model": ep.model}
    t0 = time.perf_counter()
    report["warmup"] = post(a, quick, "warmup", t0)  # proves the connection works before the test
    if report["warmup"].get("status") != 200:
        print(json.dumps(report, indent=2)); return
    out: list = []
    for round_no in (1, 2):
        t0 = time.perf_counter()
        first: list = []
        t = threading.Thread(target=lambda: first.append(post(a, slow, "first (long)", t0)))
        t.start()
        time.sleep(0.4)
        second = post(b, quick, "second (short, 0.4 s later)", t0)
        t.join()
        out.append({"round": round_no, "requests": [first[0], second]})
    report["rounds"] = out
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
