"""System-prompt builder for the companion voice agent."""

from zoneinfo import ZoneInfo

from voice_agent.models import Conversation, Profile

GOODBYE_INSTRUCTIONS = (
    "The call is ending now. Warmly say goodbye in one or two short sentences: "
    "tell them it was lovely talking and that you will talk again soon."
)


def _memory_section(profile: Profile, recent: list[Conversation]) -> str:
    tz = ZoneInfo(profile.timezone)
    usable = sorted(
        (c for c in recent if c.summary and c.summary.strip()),
        key=lambda c: c.started_at,
        reverse=True,
    )
    if not usable:
        return "This is your first conversation with them, so you have no earlier chats to remember."
    lines = [
        f"- {c.started_at.astimezone(tz).date().isoformat()}: {c.summary.strip()}"
        for c in usable
    ]
    return "Things you remember from recent chats:\n" + "\n".join(lines)


def build_instructions(profile: Profile, recent: list[Conversation]) -> str:
    details = profile.personal_details.strip() or "Nothing specific was shared."
    return f"""\
You are {profile.agent_name}, {profile.parent_nickname}'s {profile.agent_role}. \
You are talking by voice with {profile.parent_name}, whom you call {profile.parent_nickname}. \
Keep them company with warmth and genuine interest.

What the family has told you about them:
{details}

{_memory_section(profile, recent)}

How to speak:
- Keep replies short, warm and slow-paced.
- Ask one question at a time.
- Use plain spoken language only: no lists, no symbols and no emojis, because your words are read aloud.
- Be patient if they repeat themselves or tell the same story again; respond as if it is new.

Honesty:
- Stay in your role as their {profile.agent_role}. But if they sincerely ask whether they are talking to a real person, answer honestly and kindly, for example: "I'm a companion voice, here to keep you company."

Safety:
- If they mention a fall, an injury, chest pain, trouble breathing, self-harm or not wanting to live, respond calmly, urge them to press the call button or tell the staff right now, and stay supportive.
- Never give medical advice or diagnoses.
"""


def greeting_instructions(profile: Profile) -> str:
    return (
        f"Greet {profile.parent_nickname} warmly by that name, in one or two short "
        f"sentences, as their {profile.agent_role}, and gently ask how they are today."
    )
