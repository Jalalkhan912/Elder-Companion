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

Then, straight after the first start, open `http://localhost:8000/setup` to choose a PIN and fill in the profile (names, child emails, summary time, time zone). For testing on the same machine, `http://localhost:8000/talk` works as is. For the parent's tablet you need HTTPS (next section).

### HTTPS for the tablet

Browsers only allow the microphone on `https://` pages or on `localhost`. Over plain `http://<lan-ip>:8000` the talk page shows "This page needs a secure https:// link…" and cannot start a call. Use [mkcert](https://github.com/FiloSottile/mkcert) to make a locally trusted certificate:

```bash
mkcert -install                         # creates a local root CA on this machine
mkcert 192.168.1.20 localhost           # use this machine's LAN IP; writes 192.168.1.20+1.pem and 192.168.1.20+1-key.pem
uv run uvicorn voice_agent.web.app:create_app_from_env --factory --host 192.168.1.20 --port 8000 \
  --ssl-keyfile 192.168.1.20+1-key.pem --ssl-certfile 192.168.1.20+1.pem
```

Install mkcert's root CA (`mkcert -CAROOT` shows where `rootCA.pem` is) on the tablet and mark it trusted (iOS: install the profile, then Settings → General → About → Certificate Trust Settings; Android: Settings → Security → Install a certificate → CA certificate). Then give the parent `https://192.168.1.20:8000/talk`. If the parent denied the microphone, the page says "Please allow the microphone, then tap to try again"; allow it in the browser's site settings.

### Security

This is a prototype for a trusted home or care-home network. The token and setup endpoints are reachable by anyone who can reach the port (setup is PIN-protected, but until the first setup is saved whoever opens `/setup` first chooses the PIN; the token endpoint has no PIN). Bind to the specific LAN IP (as above) rather than `0.0.0.0` where you can, never expose the port to the internet, and do the setup immediately after the first start.

### Daily email timing

The daily email is sent once per local day, at or after `summary_time`. So if you save the setup after today's summary time, the first email ("… didn't talk with … today") goes out within a minute; and `00:00` sends at midnight, at the very start of the day. A failed daily send is retried each minute, up to 3 times.

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

## Schema changes

There are no migrations during the prototype phase: after pulling a change to the database schema, stop both processes and delete `data/companion.db` to reset it (this erases all data, as above).
