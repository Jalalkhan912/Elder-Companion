# Elder Companion

A voice companion for an elderly parent. The parent opens a web page, taps **Talk**, and chats with a friendly AI (for example "Ayesha", the daughter) by voice. After each call the app analyses the conversation and emails the grown children:

- an **urgent email** within about a minute if something serious came up (fall, chest pain, self-harm talk, and so on);
- a **daily summary email** at a time and time zone the child chooses, with mood, summary and any mild warnings.

It is two processes sharing one SQLite file: a LiveKit agent worker (the voice) and a FastAPI backend (talk page, PIN-protected setup page, scheduler, email via Resend).

## Prerequisites

- Python 3.11 and [uv](https://docs.astral.sh/uv/)
- A [LiveKit Cloud](https://cloud.livekit.io) project and the `lk` CLI
- A [Resend](https://resend.com) API key with a verified sender address

## Setup

```bash
uv sync
cp .env.example .env.local   # then fill in the values
uv run python -m voice_agent.agent download-files   # one-time: turn detector / VAD models
```

`.env.local` is git-ignored. Variables: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (from your LiveKit project, or `lk app env`), `RESEND_API_KEY`, `EMAIL_FROM` (the verified sender), and optionally `DB_PATH` (default `data/companion.db`).

## Run

Terminal 1, the voice agent:

```bash
uv run python -m voice_agent.agent dev
```

Terminal 2, the web backend:

```bash
uv run uvicorn voice_agent.web.app:create_app_from_env --factory --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/setup` to choose a PIN and fill in the profile (names, child emails, summary time, time zone), and give the parent `http://<this-machine>:8000/talk` (the talk page).

## Tests

```bash
uv run pytest            # fast tests, no network
uv run pytest -m slow    # needs real LiveKit credentials: agent behaviour and analysis quality
```

## Manual checks

1. Open the talk page in a browser, allow the microphone, and talk for about a minute. Say "I had some chest pain this morning", then tap End. An urgent email should arrive within about a minute.
2. In setup, set `summary_time` to two minutes from now. The daily summary email should arrive with the chat summary.

## Forgotten PIN

Stop both processes and delete `data/companion.db`. This also erases the profile and all stored conversations; set everything up again afterwards.
