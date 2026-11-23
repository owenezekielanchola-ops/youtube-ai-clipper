# Testing

ClipForge has three test layers, all wired into CI (`.github/workflows/ci.yml`).

## Backend — pytest (unit + API + middleware)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest                 # run suite
pytest --cov=.         # with coverage
```

Covers: `normalize_clip_windows` clip math (negative starts, out-of-range,
zero-duration, per-clip & total caps), `_sanitize_project_id`, the SFX library
(extension/size/traversal guards), every read/write API endpoint via FastAPI
`TestClient`, and CORS middleware (localhost allowed, foreign origins not echoed).

## Frontend — Jest (unit, editing engine)

```bash
cd frontend
npm install
npm test               # run suite
npm run test:coverage  # with coverage
```

Covers the shared Zustand editing engine: `trim_clip`, `split_clip`,
`merge_clips`, `delete_clips`, caption add/delete, `change_video_format`, and
undo/redo via zundo.

## Frontend — Playwright (E2E smoke)

```bash
cd frontend
npm run test:e2e:install   # one-time: install chromium
npm run test:e2e
```

Smoke tests build + serve the production bundle and assert the app shell mounts
with no uncaught errors. The full ingest → edit → export journey is gated:

```bash
RUN_FULL_E2E=1 E2E_YT_URL="https://youtu.be/..." npm run test:e2e
```

(requires a running backend with `GROQ_API_KEY` + `GEMINI_API_KEY` and FFmpeg.)

## CI

`backend`, `frontend`, and `e2e` jobs run on every push/PR to `main`:
lint → type-check → unit tests (+coverage) → build → E2E smoke.
