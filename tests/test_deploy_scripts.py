"""Tests for the bash helpers in deploy/server-deploy.sh."""
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_VARS = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "RESEND_API_KEY", "EMAIL_FROM"]


def _bash(script: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"SOURCE_ONLY=1 source deploy/server-deploy.sh; {script}"],
        cwd=ROOT, input=stdin, capture_output=True, text=True,
    )


def render(host: str, user: str, hash_: str) -> str:
    args = " ".join(shlex.quote(a) for a in (host, user, hash_))
    result = _bash(f"cd deploy && render_caddyfile {args}")
    assert result.returncode == 0, result.stderr
    return result.stdout


def run_write_env(stdin: str) -> subprocess.CompletedProcess:
    return _bash("write_env", stdin)


def test_render_caddyfile_keeps_special_chars():
    out = render("x-1-2-3-4.sslip.io", "family", "$2a$14$ab/c.d&e'f\"g")
    assert "x-1-2-3-4.sslip.io {" in out
    assert "family $2a$14$ab/c.d&e'f\"g" in out
    assert "reverse_proxy web:8000" in out
    assert "__" not in out


def test_write_env_excludes_site_and_requires_all():
    full = "".join(f"/elder/{k}\tv-{k}\n" for k in APP_VARS + ["SITE_USER", "SITE_PASSWORD"])
    ok = run_write_env(full)
    assert ok.returncode == 0
    assert "SITE_" not in ok.stdout
    assert "LIVEKIT_URL=v-LIVEKIT_URL" in ok.stdout and "DB_PATH=/data/companion.db" in ok.stdout
    bad = run_write_env(full.replace("/elder/RESEND_API_KEY\tv-RESEND_API_KEY\n", ""))
    assert bad.returncode == 1 and "missing: RESEND_API_KEY" in bad.stderr
