"""IELTS Prep Coach backend — minimal FastAPI service.

Endpoints:
  GET  /health                 — liveness check
  POST /ielts/writing-feedback — AI band-score grading for IELTS Writing Task 1/2

Env:
  GROQ_API_KEY  — Groq API key (openai-compatible endpoint)
"""
import json as json_lib
import logging
import os
import re
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

log = logging.getLogger("ielts-backend")

app = FastAPI(title="IELTS Coach API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Chrome extension calls directly; no cookies/auth
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ---------- simple in-memory rate limiting ----------
_rate_buckets: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_check(key: str, limit: int, window_sec: int = 60) -> bool:
    now = time.time()
    stamps = [t for t in _rate_buckets.get(key, []) if now - t < window_sec]
    if len(stamps) >= limit:
        _rate_buckets[key] = stamps
        return False
    stamps.append(now)
    _rate_buckets[key] = stamps
    return True


# ---------- models ----------
class IeltsWritingReq(BaseModel):
    task_type: str = Field(..., description="task1 or task2")
    prompt: str = Field(..., description="The IELTS writing prompt text")
    essay: str = Field(..., description="The student's essay text")


class IeltsWritingResp(BaseModel):
    band: float
    feedback: str
    strengths: list[str]
    improvements: list[str]


IELTS_WRITING_SYSTEM = (
    "You are a strict IELTS examiner grading a Writing {task} essay. "
    "Score on four criteria: Task Achievement, Coherence and Cohesion, "
    "Lexical Resource, Grammatical Range and Accuracy. "
    "Return ONLY valid JSON (no markdown, no code fences, no extra text) "
    "with exactly these keys: "
    '{"band": <overall band 0-9 in 0.5 steps>, '
    '"feedback": "<2-3 sentences of overall feedback>", '
    '"strengths": ["<strength 1>", "<strength 2>"], '
    '"improvements": ["<improvement 1>", "<improvement 2>"]}'
)


def _clamp_band(value) -> float:
    try:
        b = float(value)
    except (TypeError, ValueError):
        b = 5.0
    b = max(0.0, min(9.0, b))
    return round(b * 2) / 2


def _parse_ielts_grading(text: str) -> dict:
    """Defensively parse the examiner LLM's JSON. Falls back gracefully."""
    data = None
    if text:
        try:
            data = json_lib.loads(text)
        except Exception:
            data = None
        if not isinstance(data, dict):
            try:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                data = json_lib.loads(m.group(0)) if m else None
            except Exception:
                data = None
            if not isinstance(data, dict):
                data = None
    if data is None:
        data = {}
    strengths = data.get("strengths")
    improvements = data.get("improvements")
    return {
        "band": _clamp_band(data.get("band", 5.0)),
        "feedback": str(data.get("feedback") or "Keep practising — focus on clear structure and accurate grammar.").strip(),
        "strengths": [str(s) for s in strengths][:2] if isinstance(strengths, list) else [],
        "improvements": [str(s) for s in improvements][:2] if isinstance(improvements, list) else [],
    }


# ---------- routes ----------
@app.get("/health")
def health():
    return {"ok": True, "service": "ielts-coach-api"}


@app.post("/ielts/writing-feedback", response_model=IeltsWritingResp)
async def ielts_writing_feedback(req: IeltsWritingReq, request: Request):
    """Grade an IELTS Writing Task 1/2 essay. Returns band 0-9 (0.5 steps) +
    feedback + 2 strengths + 2 improvements. Rate-limited: 10/IP/min."""
    if not _rate_check(f"ielts-writing:{_client_ip(request)}", 10):
        raise HTTPException(429, "Too many requests — slow down.")
    task_type = (req.task_type or "").strip().lower()
    if task_type not in ("task1", "task2"):
        raise HTTPException(400, "task_type must be 'task1' or 'task2'")
    essay = (req.essay or "").strip()
    if len(essay) < 50:
        raise HTTPException(400, "essay too short")
    if len(essay) > 20000:
        raise HTTPException(400, "essay too long")
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        raise HTTPException(503, "grading service unavailable")
    system = IELTS_WRITING_SYSTEM.format(task="Task 1" if task_type == "task1" else "Task 2")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-oss-20b",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": "Prompt:\n" + (req.prompt or "").strip()[:2000] +
                         "\n\nEssay:\n" + essay},
                    ],
                    "max_tokens": 700,
                    "temperature": 0.3,
                },
            )
    except Exception:
        log.exception("ielts-writing: groq request failed")
        raise HTTPException(502, "grading service error")
    if r.status_code != 200:
        log.warning("ielts-writing: groq non-200")
        raise HTTPException(502, "grading service error")
    try:
        body = r.json()
        text = body["choices"][0]["message"]["content"]
    except Exception:
        log.warning("ielts-writing: unexpected groq response shape")
        raise HTTPException(502, "grading service error")
    return _parse_ielts_grading(text)
