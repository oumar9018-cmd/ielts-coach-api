# IELTS Coach API

Minimal backend for the **IELTS Practice Coach** Chrome extension.

## Endpoints

- `GET /health` — liveness check
- `POST /ielts/writing-feedback` — grades an IELTS Writing Task 1/2 essay with AI, returns band score (0–9), feedback, strengths and improvements

## Deploy (Render)

1. Push to GitHub
2. Render → New Web Service → connect this repo
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Set env var `GROQ_API_KEY`

## Local

```bash
pip install -r requirements.txt
GROQ_API_KEY=xxx uvicorn main:app --reload
```
