# Docker, CI/CD and AWS deployment — design

Date: 2026-10-29

## Goal

Run Elder Companion in production on the owner's AWS account (credits plan), deployed automatically from GitHub, reachable over HTTPS from the parent's tablet, and protected by a site-wide password. Success means: a push to `main` that passes tests ends up live without manual steps, and a real call on the production site produces the urgent email.

## Decisions (agreed with the owner)

- Repo: `https://github.com/Jalalkhan912/Elder-Companion` (public). Secrets never enter the repo.
- AWS: account 423437245111, region `us-east-1`, credits plan.
- Server: one EC2 **t3.small** (2 GB), Amazon Linux 2023, 20 GB gp3, Elastic IP, 1 GB swap.
- Address: `https://<ip-with-dashes>.sslip.io`; Caddy obtains the Let's Encrypt certificate.
- Protection: HTTP basic auth on every path, enforced by Caddy.
- Infrastructure as one CloudFormation template (create and delete with one command each).
- No changes to application code.

## Architecture

```
tablet --https--> Caddy (basic auth, TLS) --> web (uvicorn :8000) --+
                                                                     +-- volume /data (SQLite)
                                              agent (LiveKit start) --+
tablet <--- WebRTC audio ---> LiveKit Cloud <---> agent
```

- **Image:** one image, `python:3.11-slim` base, dependencies via `uv sync --frozen --no-dev`, models fetched at build time with `python -m voice_agent.agent download-files`. Runs as a non-root user.
- **Compose services** (same image):
  - `web`: `uvicorn voice_agent.web.app:create_app_from_env --factory --host 0.0.0.0 --port 8000`, not published to the host.
  - `agent`: `python -m voice_agent.agent start`.
  - `caddy`: official image, ports 80/443, Caddyfile with the sslip.io host and `basic_auth`, its own volume for certificates.
  - Both app services: `DB_PATH=/data/companion.db`, shared named volume `appdata`, `restart: unless-stopped`, env from `/opt/elder/.env`.
- **Basic auth and the talk page:** the token request is a same-origin fetch, so the browser sends the stored credentials. Audio goes directly to LiveKit Cloud and is not affected.

## AWS resources (`infra/cloudformation.yml`)

- VPC: the account's default VPC and a default subnet (passed as parameters, discovered by the deploy instructions).
- Security group: inbound 80 and 443 from anywhere; no SSH.
- EC2 instance with user data that installs Docker and the compose plugin, creates swap, and creates `/opt/elder`.
- Elastic IP attached to the instance.
- Instance role: `AmazonSSMManagedInstanceCore`, ECR read-only, read of SSM parameters under `/elder/`, and KMS decrypt for the default SSM key.
- ECR repository `elder-companion` with a lifecycle rule that keeps the last 5 images.
- GitHub OIDC provider (created only if a parameter says it does not already exist) and a deploy role. Trust is limited to `repo:Jalalkhan912/Elder-Companion:ref:refs/heads/main`. Permissions: push to this ECR repository, put parameters under `/elder/`, and `ssm:SendCommand` and read command results for this instance only.
- Outputs: instance ID, public IP, site URL, ECR URI, deploy role ARN.

## Pipeline (`.github/workflows/deploy.yml`)

1. **test** job (every push and pull request): install uv, `uv sync`, `uv run pytest`.
2. **deploy** job (push to `main`, needs test):
   - Assume the deploy role through OIDC.
   - Build the image and push it to ECR, tagged with the commit SHA.
   - Write the GitHub secrets to SSM Parameter Store as SecureStrings under `/elder/`.
   - Run `deploy/server-deploy.sh` on the instance via `ssm send-command` (the script and compose files are sent in the command), then wait for it to finish and fail the job if it failed.
3. **smoke** step, from the runner:
   - `GET /api/status` with credentials returns 200 over valid HTTPS (retry for up to 3 minutes while the certificate is issued).
   - The same request without credentials returns 401.

**GitHub secrets the owner adds:** `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `RESEND_API_KEY`, `EMAIL_FROM`, `SITE_USER`, `SITE_PASSWORD`.

**GitHub variables (not secret), set by Claude from the stack outputs:** `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`, `ECR_URI`, `INSTANCE_ID`, `SITE_HOST`.

## Server deploy script (`deploy/server-deploy.sh`)

1. Read `/elder/*` parameters and write `/opt/elder/.env` with mode 600.
2. Hash `SITE_PASSWORD` using the Caddy image's `caddy hash-password` command.
3. Render the Caddyfile with the host, user and hash.
4. Log in to ECR, set `IMAGE=<uri>:<sha>`, run `docker compose pull` and `docker compose up -d`, then prune old images.
5. Exit non-zero if any service fails to reach the running state within 60 seconds.

## Error handling

- Failed tests block the deploy.
- A failed deploy script or smoke test fails the workflow, which is visible in GitHub Actions. The previous containers keep running if the pull fails.
- Containers restart automatically after crashes or reboots.

## Data

The SQLite database lives in the `appdata` volume on the instance disk and survives deploys and reboots. There are no backups. Deleting the stack deletes the data; the README says so.

## Testing in production

1. The pipeline smoke test passes.
2. Claude checks over SSM that:
   - the containers are running;
   - the agent log shows it registered with LiveKit;
   - `/talk` and `/setup` load;
   - `/api/token` returns a token when credentials are given.
3. The owner, with the local agent stopped:
   - completes `/setup` straight away;
   - makes a call that mentions chest pain;
   - confirms the urgent email arrives.
4. Claude confirms from the production agent logs that the call was handled there.

## Known risks

- The local dev agent and production share a LiveKit project and agent name, so a running local agent may take production calls. Mitigation: stop it while testing, or use a separate LiveKit project later.
- If the instance is replaced, the Elastic IP keeps the sslip.io address stable.
- Costs about $20 a month in credits. Running `aws cloudformation delete-stack` stops all charges.

## Files

- `Dockerfile`
- `.dockerignore`
- `deploy/docker-compose.yml`
- `deploy/Caddyfile.template`
- `deploy/server-deploy.sh`
- `infra/cloudformation.yml`
- `.github/workflows/deploy.yml`
- README "Deploying" section
