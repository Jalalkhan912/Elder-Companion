# Elder Companion Voice Agent — Design

Date: 2026-10-29
Status: Draft for review

## 1. Purpose

Elderly parents living in old-age homes often feel lonely because their children
cannot visit or call as often as they would like. This product gives the parent a
warm voice companion on a tablet. The companion plays a role chosen by the child
(son, daughter, friend, companion) and remembers recent conversations. The child
receives a daily email summary of the parent's conversations at a time they choose,
plus an immediate email if the parent says something serious.

### Success criteria

- The parent can start a conversation with a single tap and talk naturally.
- The companion greets the parent by name, stays in its role, and refers back to
  recent conversations.
- Serious warnings (falls, chest pain, self-harm talk, etc.) reach every listed
  child by email within about a minute of the conversation ending.
- A daily summary email arrives at the child's chosen time, every day, including
  days with no conversations.

### Decisions made during brainstorming

| Topic | Decision |
|---|---|
| How the parent talks | Tablet / web app with one big Talk button |
| Languages (v1) | English only; language is stored per profile so others can be added later |
| Scope | Working prototype; one tablet = one parent; no accounts |
| Setup | Child fills in a setup form on the parent's tablet, protected by a PIN |
| Children | One or more child email addresses per parent |
| Warnings | Urgent warnings emailed immediately; mild concerns go in the daily summary |
| Memory | Short memory: summaries of the last 5 conversations fed into each new chat |
| Voice stack | LiveKit Cloud + LiveKit Inference (STT → LLM → TTS pipeline) |
| Email | Resend |
| Honesty | Agent stays in role but never claims to be a real human when sincerely asked |

### Out of scope for v1

- Languages other than English
- Child login or remote management of settings
- Multiple parents per tablet
- Alerts to care-home staff
- Production hosting/deployment (everything runs locally first)
- Phone/SIP calling

## 2. Architecture

Two Python processes in this repository share one SQLite database file.

```
 Tablet browser ──HTTP──▶ Web backend (FastAPI)  ◀──── Scheduler (inside backend)
   │  /setup (PIN)          • serves setup + talk pages        • daily summary email
   │  /talk (big button)    • issues LiveKit room tokens       • analysis retries
   │                        • reads/writes SQLite
   └──WebRTC audio──▶ LiveKit Cloud ──dispatch──▶ Agent worker (LiveKit Agents, Python)
                                                   • loads profile + recent memory
                                                   • talks (Inference STT→LLM→TTS)
                                                   • on end: saves transcript,
                                                     runs analysis, sends urgent email
```

Runtime: Python 3.11 (LiveKit Agents requires ≥ 3.10; the current `.python-version`
of 3.9 must be bumped). Dependencies managed with `uv`.

## 3. Components

All code lives under `src/voice_agent/`. Each module has one job and can be tested
on its own.

### 3.1 `config`
Loads settings from `.env.local`: `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`, `RESEND_API_KEY`, `EMAIL_FROM`, `DB_PATH` (default
`data/companion.db`). Fails fast with a clear message if a required value is missing.

### 3.2 `db`
SQLite access through a small repository layer (standard-library `sqlite3`).

Tables:

- `profile` (single row)
  - `parent_name`, `parent_nickname` (what the agent calls them, e.g. "Abbu")
  - `agent_role` (`son` | `daughter` | `friend` | `companion`), `agent_name`
  - `personal_details` (free text: family names, hobbies, history, health notes)
  - `language` (default `en`)
  - `child_emails` (JSON list, at least one)
  - `summary_time` (HH:MM), `timezone` (IANA name)
  - `pin_hash`, `created_at`, `updated_at`
- `conversations`
  - `id`, `room_name`, `started_at`, `ended_at`, `end_reason`
  - `transcript` (JSON list of `{role, text, at}`)
  - `status` (`pending_analysis` | `analyzed` | `analysis_failed`), `analysis_attempts`
  - `summary`, `mood` (`good` | `okay` | `low`), `warnings` (JSON list)
- `email_log`
  - `id`, `kind` (`urgent` | `daily`), `conversation_id` (nullable),
    `period_date` (for daily), `recipients`, `sent_at`, `status`, `error`
  - Unique constraint on (`kind`, `period_date`) for daily and
    (`kind`, `conversation_id`) for urgent, so no email is sent twice.

### 3.3 `persona`
Pure function: `build_instructions(profile, recent_summaries) -> str`.

The instructions include:
- Role and identity: "You are {agent_name}, {parent_nickname}'s {agent_role}…"
- Personal details from the child.
- Summaries of the last 5 analyzed conversations, newest first, as "things you
  remember".
- Style rules: short, warm, slow-paced replies; one question at a time; plain
  spoken language with no lists, symbols or emojis; patient with repetition.
- Honesty rule: stay in role, but if the parent sincerely asks whether they are
  talking to a real person, answer honestly and kindly (e.g. "I'm Ayesha's
  companion voice, here to keep you company").
- Safety rules: if the parent mentions a fall, injury, chest pain, trouble
  breathing, self-harm or not wanting to live, respond calmly, urge them to press
  the call button or tell the staff right now, and stay supportive. Never give
  medical advice or diagnoses.

### 3.4 `agent`
The LiveKit Agents worker (`AgentServer`, `@server.rtc_session`).

- Pipeline via LiveKit Inference: STT (AssemblyAI Universal, `language="en"`),
  LLM (Inference default), TTS (a warm English voice), plus the turn detector.
  Model names live in one place so they can be changed easily.
- On session start: load profile and recent summaries from `db`, build
  instructions with `persona`, create a `conversations` row, and greet the parent
  by nickname in role.
- Collect transcript turns as the session runs.
- Session ends when: the parent taps End (participant leaves), the parent says
  goodbye and the agent closes the call, about 2 minutes of silence, or a 45-minute
  hard cap.
- On end (shutdown callback): save the transcript and set status
  `pending_analysis`, then run `analysis`. If it finds urgent warnings, call
  `notify.send_urgent`. Analysis and email failures are logged and never crash
  the worker.

### 3.5 `analysis`
`analyze(transcript) -> AnalysisResult` with one LLM call that returns JSON:

```json
{
  "summary": "3–5 plain sentences",
  "mood": "good | okay | low",
  "warnings": [{"level": "urgent | mild", "reason": "…", "quote": "…"}]
}
```

- **Urgent:** fall or injury, chest pain, breathing trouble, self-harm or suicidal
  talk, severe confusion, abuse or mistreatment.
- **Mild:** low mood, loneliness, poor sleep or appetite, ongoing pain, medication
  worries, complaints about care.
- Output is validated with a Pydantic model. Invalid output counts as a failure.
- The LLM client is injected so tests can use a fake one.

### 3.6 `notify`
Builds and sends emails via Resend (client injected for tests).

- `send_urgent(profile, conversation)`: subject like
  "⚠️ Urgent: Abbu mentioned chest pain". The body has the reason, the quote, the
  time, and a suggestion to call the care home. Sent to all child emails.
- `send_daily(profile, date)`: covers every conversation since the last daily
  email. For each one: time, length, summary. Then overall mood, mild warnings, and
  any chats that could not be summarized. If there were no conversations, it says
  so plainly ("Abbu didn't talk with Ayesha today").
- Every send is written to `email_log`. It is retried up to 3 times with backoff.
  The unique constraints prevent duplicates.

### 3.7 `web`
FastAPI app plus an in-process scheduler (APScheduler).

Routes:
- `GET /`: redirects to `/talk` if a profile exists, otherwise to `/setup`.
- `GET/POST /setup`: setup form. Before a profile exists, the first save also sets
  the PIN. After that, the form needs the PIN. 5 wrong tries lock it for 5 minutes.
- `GET /talk`: the parent's page.
- `POST /api/token`: creates a unique room name and returns a LiveKit access token
  and URL. The agent is dispatched to the room by name. Refused if no profile
  exists.

Scheduler jobs:
- Every minute: if it is past `summary_time` in the profile's time zone and today's
  daily email has not been sent, run `send_daily`.
- Every 5 minutes: retry analysis for conversations in `pending_analysis` with
  fewer than 3 attempts. After 3, mark them `analysis_failed`. Send urgent emails
  if retried analysis finds urgent warnings.

### 3.8 Frontend
Static HTML/JS/CSS served by `web`, using the LiveKit browser SDK from a CDN.

- **Talk page:** one very large Talk/End button, large high-contrast text, a
  simple status line ("Connecting…", "Ayesha is listening", "Ayesha is
  speaking"). No other controls.
- **Connection failure:** a big friendly message ("Couldn't connect — tap to try
  again") and one automatic retry.
- **Setup page:** a plain form with the profile fields and a PIN, plus a small
  "settings" link on the talk page.

## 4. Data flow: one conversation

1. Parent taps Talk. The page calls `/api/token`, joins the room and enables the
   microphone.
2. LiveKit dispatches the agent worker. The agent loads the profile and memory,
   builds instructions and greets the parent.
3. The conversation runs. Transcript turns are collected.
4. The conversation ends (End button, goodbye, silence or cap).
5. The agent saves the transcript and runs analysis. Urgent warnings trigger an
   immediate email.
6. At `summary_time`, the scheduler sends the daily summary covering all
   conversations since the last one.

## 5. Error handling

- **The conversation comes first:** failures in analysis, email or the database
  are logged and never interrupt or crash a live conversation.
- **Connection drop or agent not joining:** the talk page shows a retry message
  and retries once automatically.
- **Analysis failure:** the transcript is already saved. The scheduler retries
  every 5 minutes, up to 3 attempts. If all fail, the daily email lists the chat
  as "couldn't be summarized".
- **Email failure:** retried with backoff and recorded in `email_log`. Unique
  constraints prevent duplicate sends.
- **No profile:** `/talk` and `/api/token` send the user to setup.
- **PIN:** stored as a salted hash. 5 failed attempts lock setup for 5 minutes.
- **Secrets:** stay in `.env.local`, which is git-ignored.

## 6. Testing

- **Unit tests (pytest):**
  - `persona`: role, names, memory and safety/honesty rules appear in the prompt.
  - `analysis`: parsing, validation, failure handling, and urgent vs mild
    classification, using a fake LLM.
  - `notify`: email content, the no-conversation day, and send-once behaviour,
    using a fake Resend client.
  - The scheduler's due-time logic across time zones.
  - `db`: the repository functions, against a temporary SQLite file.
  - `web`: setup/PIN flow and the token endpoint (FastAPI TestClient).
- **Analysis quality tests (marked `slow`, real LLM):** sample transcripts for a
  happy chat, a fall, sadness and suicidal talk. Each must produce the expected
  warning level.
- **Agent behaviour tests:** use LiveKit Agents' testing helpers to check the
  agent greets by nickname and responds safely to an emergency phrase.
- **Manual end to end:** `lk agent dev` plus the web app in a browser. Have a
  conversation, then confirm the urgent and daily emails arrive.
