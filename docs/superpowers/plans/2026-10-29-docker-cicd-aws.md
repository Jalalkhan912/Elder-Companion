# Docker, CI/CD and AWS Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Elder Companion to one EC2 t3.small behind Caddy (HTTPS and basic auth), deployed by GitHub Actions on every push to `main`.

**Architecture:** One Docker image runs as two compose services (`web` and `agent`) that share a SQLite volume, with Caddy in front. A CloudFormation stack owns all AWS resources. GitHub Actions tests, builds, pushes to ECR, syncs secrets to SSM Parameter Store, deploys via `ssm send-command`, and runs a smoke test.

**Tech Stack:** Docker, Docker Compose v2, Caddy 2.10, CloudFormation, GitHub Actions (OIDC), AWS ECR, SSM, Amazon Linux 2023, uv 0.12.17, Python 3.11.

**Spec:** `docs/superpowers/specs/2026-10-29-docker-cicd-aws-design.md`

## Global Constraints

- Region `us-east-1`, account `423437245111`, instance type `t3.small`, 20 GB gp3, 1 GB swap.
- Repo `Jalalkhan912/Elder-Companion`. The OIDC trust subject is exactly `repo:Jalalkhan912/Elder-Companion:ref:refs/heads/main`.
- Site host: `<eip-with-dashes>.sslip.io`. Basic auth applies to every path.
- No application code changes under `src/`.
- No secret values in the repo, workflow logs or command parameters. Secrets travel GitHub secrets → SSM `/elder/<NAME>` (SecureString) → `/opt/elder/.env` (mode 600).
- GitHub secrets: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `RESEND_API_KEY`, `EMAIL_FROM`, `SITE_USER`, `SITE_PASSWORD`.
- GitHub variables: `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`, `ECR_URI`, `INSTANCE_ID`, `SITE_HOST`.
- Container DB path: `DB_PATH=/data/companion.db`. ECR repository name `elder-companion`, keeping the last 5 images.
- Pin versions: `python:3.11-slim-bookworm`, `ghcr.io/astral-sh/uv:0.12.17`, `caddy:2.10`, and Docker Compose v2.40.3 on the server.

## Review Focus

1. **A site password containing `$`, `/`, `&` or quotes**, and its bcrypt hash (which always contains `$`), must reach the Caddyfile unchanged. Pinned by `test_render_caddyfile_keeps_special_chars` (Task 3).
2. **Deploying again** must keep the database and must not fail on files left by the previous deploy. Pinned by deploying twice in Task 5, step 6.
3. **After the server reboots**, all three containers come back without a deploy. Pinned in Task 6, step 2.
4. **The first HTTPS certificate can take a minute or more** to issue (Caddy falls back to ZeroSSL if Let's Encrypt fails). The smoke test retries for 3 minutes instead of failing straight away. Pinned in Task 5, step 2.
5. **A missing or empty GitHub secret** must fail the deploy with the secret's name, not write an empty `.env`. Pinned by the `check-secrets` step (Task 5) and `test_write_env_excludes_site_and_requires_all` (Task 3).

---

### Task 1: Push to GitHub and run the tests in CI

**Files:**
- Create: `.github/workflows/deploy.yml` (the `test` job only)

**Interfaces:**
- Produces: a workflow named `ci-cd` with a `test` job. Task 5 adds jobs that `needs: test`.

- [ ] **Step 1: Scan the git history for secrets.** Run `git log -p --all | grep -nE 're_[A-Za-z0-9]{20,}|API[0-9A-Za-z]{10,}|secret.{0,5}[:=].{20,}' | head`. Expected: no real key values (fixture placeholders only). If anything real shows up, stop and tell the owner.
- [ ] **Step 2: Write the `test` job.**
  - Triggers: `push` to any branch, and `pull_request`.
  - Steps: `actions/checkout@v4`, `astral-sh/setup-uv@v6` with `version: "0.12.17"`, `uv sync --frozen`, `uv run pytest`.
  - Top-level permissions: `contents: read`.
- [ ] **Step 3: Add the remote and push.** Run `git remote add origin https://github.com/Jalalkhan912/Elder-Companion.git` then `git push -u origin main`.
- [ ] **Step 4: Verify.** Run `gh run watch --exit-status $(gh run list -L1 --json databaseId -q '.[0].databaseId')`. Expected: the `test` job succeeds.

### Task 2: Docker image

**Files:**
- Create: `Dockerfile`, `.dockerignore`
- Modify: `.github/workflows/deploy.yml` (add a `build-check` job; Task 5 replaces it)

**Interfaces:**
- Produces: an image that runs as user `app` (uid 1000) with workdir `/app` and `HF_HOME=/app/.cache/huggingface`. `/data` exists and is owned by `app`. The default `CMD` is the web command from the spec. The agent command is passed in compose (Task 3).

- [ ] **Step 1: Write the `build-check` job (the failing test).**
  - On push: `docker build -t ec:test .`
  - Then `docker run --rm ec:test python -c "import voice_agent.agent, voice_agent.web.app, onnxruntime"`
  - Then `docker run --rm ec:test sh -c 'id -u && test -w /data'`; expected output `1000`.
- [ ] **Step 2: Push and watch it fail.** There is no Dockerfile yet. Expected: `build-check` fails at `docker build`.
- [ ] **Step 3: Write the `Dockerfile`.**
  - Builder pattern: copy `uv` from the pinned image.
  - `uv sync --frozen --no-dev --no-install-project`, then copy the source and run `uv sync --frozen --no-dev`.
  - Put `/app/.venv/bin` first on `PATH`.
  - Install `libgomp1` if onnxruntime needs it.
  - Create user `app` and `/data`.
  - Run `python -m voice_agent.agent download-files` **as `app`**, so the model cache belongs to it.
- [ ] **Step 4: Write `.dockerignore`.** Exclude `.venv`, `.git`, `data`, `.env*`, `.pytest_cache`, `.superpowers`, `docs`, `tests` and `__pycache__`.
- [ ] **Step 5: Push and verify.** Expected: `build-check` succeeds and prints `1000`.
- [ ] **Step 6: Commit** (`feat: Docker image`). Steps 2 and 5 push, so commit before each push.

### Task 3: Server-side deploy files

**Files:**
- Create: `deploy/docker-compose.yml`, `deploy/Caddyfile.template`, `deploy/server-deploy.sh`
- Test: `tests/test_deploy_scripts.py`

**Interfaces:**
- Consumes: the image from Task 2.
- Produces: `deploy/server-deploy.sh`, which Task 5 runs on the instance.
  - It is run as `IMAGE=<ecr-uri>:<sha> SITE_HOST=<host> AWS_REGION=<region> bash /opt/elder/server-deploy.sh` from `/opt/elder`.
  - It reads SSM `/elder/*`.
  - When sourced with `SOURCE_ONLY=1`, it defines functions only and does not run `main`.
- Bash functions:
  - `render_caddyfile HOST USER HASH`: reads `Caddyfile.template` and writes the result to stdout. The placeholders are `__SITE_HOST__`, `__SITE_USER__` and `__SITE_HASH__`, substituted literally. Use bash parameter expansion, not sed.
  - `write_env`: reads tab-separated `/elder/NAME<TAB>VALUE` lines on stdin and writes `NAME=VALUE` lines on stdout.
    - Leave out `SITE_USER` and `SITE_PASSWORD`.
    - Add `DB_PATH=/data/companion.db`.
    - If any of the five app variables is missing or empty, exit 1 and print `missing: <NAME>` on stderr.

- [ ] **Step 1: Write the failing tests.** Run bash with `subprocess.run(["bash", "-c", "SOURCE_ONLY=1 source deploy/server-deploy.sh; ..."])` from the repo root.

```python
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
```

- [ ] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/test_deploy_scripts.py -v`. Expected: FAIL (the script does not exist).
- [ ] **Step 3: Write `Caddyfile.template`.**
  - The site block is `__SITE_HOST__`.
  - It contains `basic_auth { __SITE_USER__ __SITE_HASH__ }` and `reverse_proxy web:8000`.
- [ ] **Step 3b: Write `docker-compose.yml`.**
  - Services `web` and `agent` use `image: ${IMAGE}`, `env_file: .env`, the `appdata:/data` volume and `restart: unless-stopped`.
  - `agent` uses the command `python -m voice_agent.agent start`.
  - `caddy` uses `caddy:2.10`, ports `80:80` and `443:443`, `./Caddyfile:/etc/caddy/Caddyfile:ro`, the volumes `caddy_data:/data` and `caddy_config:/config`, and `restart: unless-stopped`.
- [ ] **Step 4: Write `server-deploy.sh`.**
  - Start with `set -euo pipefail` and define the two functions.
  - `main` does the following:
    1. Fetch the parameters with `aws ssm get-parameters-by-path --path /elder/ --with-decryption --query 'Parameters[*].[Name,Value]' --output text`.
    2. Pipe them through `write_env` into `.env` (umask 077).
    3. Get `SITE_USER` and `SITE_PASSWORD` the same way.
    4. Hash the password with `docker run --rm caddy:2.10 caddy hash-password --plaintext "$pw"`.
    5. Render the Caddyfile.
    6. Run `aws ecr get-login-password | docker login`.
    7. Run `docker compose pull`, `docker compose up -d --remove-orphans` and `docker image prune -f`.
    8. Poll `docker compose ps --status running --services` for up to 60 seconds until all three services are listed. Otherwise print `docker compose logs --tail 50` and exit 1.
- [ ] **Step 5: Run the tests and confirm they pass.** Run `uv run pytest -v`. Expected: all pass, including the 2 new tests. Also run `bash -n deploy/server-deploy.sh` (no output).
- [ ] **Step 6: Commit** (`feat: server deploy files`).

### Task 4: CloudFormation stack

**Files:**
- Create: `infra/cloudformation.yml`

**Interfaces:**
- Produces the stack `elder-companion` with these outputs: `InstanceId`, `PublicIp`, `SiteHost` (`!Join` of the EIP with dots replaced by dashes plus `.sslip.io`, built with `!Split`/`!Join`), `EcrUri` and `DeployRoleArn`.
- Parameters:
  - `VpcId` and `SubnetId` (the default VPC and a default subnet).
  - `CreateOidcProvider` (`true`/`false`).
  - `GitHubRepo` (default `Jalalkhan912/Elder-Companion`).
  - `AmiId` of type `AWS::SSM::Parameter::Value<AWS::EC2::Image::Id>`, default `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`.

- [ ] **Step 1: Write the template.** It contains the resources listed in the spec's "AWS resources" section, plus:
  - **User data:**
    - `dnf install -y docker`, then `systemctl enable --now docker`.
    - Install the Compose v2.40.3 binary to `/usr/local/lib/docker/cli-plugins/docker-compose`.
    - Create a 1 GB `/swapfile` and add it to fstab.
    - `mkdir -p /opt/elder`.
  - **Deploy role:**
    - `ecr:GetAuthorizationToken` on `*`, plus the ECR push actions on the repository ARN.
    - `ssm:PutParameter` on `parameter/elder/*`.
    - `ssm:SendCommand` on the instance ARN and on `arn:aws:ssm:us-east-1::document/AWS-RunShellScript`.
    - `ssm:GetCommandInvocation` on `*`.
  - **OIDC provider:** a condition on `CreateOidcProvider`. The provider ARN is built with `!If` for the trust policy, audience `sts.amazonaws.com`.
- [ ] **Step 2: Validate.** Run `aws cloudformation validate-template --template-body file://infra/cloudformation.yml`. Expected: JSON listing the parameters, no error.
- [ ] **Step 3: Create the stack.**
  - Discover the inputs: `aws ec2 describe-vpcs --filters Name=isDefault,Values=true`, a default subnet in that VPC, and whether `aws iam list-open-id-connect-providers` already contains `token.actions.githubusercontent.com`.
  - Run `aws cloudformation deploy --stack-name elder-companion --template-file infra/cloudformation.yml --capabilities CAPABILITY_NAMED_IAM --parameter-overrides ...`.
  - Expected: `Successfully created/updated stack`.
- [ ] **Step 4: Verify the instance.**
  - Run `aws ssm describe-instance-information` and wait until the instance is `Online`.
  - Run `aws ssm send-command` with `docker compose version && swapon --show`.
  - Expected: Compose v2.40.3 and a 1G swap line.
- [ ] **Step 5: Set the GitHub variables.** For each of the five, run `gh variable set NAME --body <value>` with the value from the stack outputs. Verify with `gh variable list`.
- [ ] **Step 6: Commit** (`feat: CloudFormation stack`).

### Task 5: Deploy job, smoke test, first deploy

**Files:**
- Modify: `.github/workflows/deploy.yml` (replace `build-check` with `deploy`)

**Interfaces:**
- Consumes: the Task 2 image, the Task 3 `server-deploy.sh` contract and the Task 4 variables.

- [ ] **Step 1: Write the `deploy` job.**
  - Conditions: `if: github.ref == 'refs/heads/main' && github.event_name == 'push'`, `needs: test`, permissions `id-token: write` and `contents: read`.
  - Steps, in order:
    1. **check-secrets:** loop over the 7 names and fail with `missing secret: NAME` if any is empty.
    2. Assume the role with `aws-actions/configure-aws-credentials@v4`.
    3. Log in with `aws-actions/amazon-ecr-login@v2`.
    4. Build and push `${ECR_URI}:${GITHUB_SHA}`.
    5. Run `aws ssm put-parameter --type SecureString --overwrite` for each secret, passing values via `env:`, never inline in `run:`.
    6. Send the command, using `AWS-RunShellScript`. The command:
       - unpacks a base64 tarball of `deploy/` into `/opt/elder`;
       - then runs `cd /opt/elder && IMAGE=... SITE_HOST=... AWS_REGION=... bash server-deploy.sh`.
    7. Poll `aws ssm get-command-invocation` every 10 seconds for up to 10 minutes.
       - On failure, print `StandardOutputContent` and `StandardErrorContent`, then exit 1.
- [ ] **Step 2: Write the smoke step.**
  - Loop for up to 180 seconds: `curl -fsS -u "$SITE_USER:$SITE_PASSWORD" https://$SITE_HOST/api/status`, which must return 200 with valid TLS (no `-k`).
  - Then `curl -s -o /dev/null -w '%{http_code}' https://$SITE_HOST/api/status` must print `401`.
- [ ] **Step 3: Owner checkpoint.** Stop here and ask the owner to add the 7 GitHub secrets (Settings → Secrets and variables → Actions). Verify with `gh secret list`: all 7 names must be present.
- [ ] **Step 4: Commit and push** (`feat: deploy pipeline`).
- [ ] **Step 5: Verify.** Run `gh run watch --exit-status <id>`. Expected: `test` and `deploy` succeed, including the smoke step.
- [ ] **Step 6: Redeploy check.**
  - Run `gh run rerun <id>`. Expected: it succeeds again.
  - Then check over SSM that `docker volume inspect elder_appdata` (compose project name `elder`) still exists and has the same creation time.

### Task 6: Production verification and README

**Files:**
- Modify: `README.md` (add a "Deploying" section)

- [ ] **Step 1: Server checks over SSM.**
  - Run `docker compose ps`; expected: 3 services running.
  - Run `docker compose logs agent | grep -i registered`; expected: one worker-registered line.
  - From this machine, `curl -u` on `/talk` and `/setup` returns 200, and `POST /api/token` returns JSON with a `token` field. Take the request body from `src/voice_agent/web/app.py:101`.
- [ ] **Step 2: Reboot check.** Run `aws ec2 reboot-instances`. When SSM shows the instance `Online` again, run `docker compose ps`. Expected: 3 services running.
- [ ] **Step 3: Owner test.** The owner stops the local agent, completes `/setup`, makes a call that mentions chest pain, and confirms the urgent email arrived. Then check over SSM with `docker compose logs agent --since 30m` for that session.
- [ ] **Step 4: README.** Add a "Deploying" section covering:
  - the architecture, in one paragraph;
  - creating the stack (the Task 4 command);
  - the GitHub secrets and variables;
  - that a push to `main` deploys;
  - how to see logs (`aws ssm start-session`, then `cd /opt/elder && docker compose logs`);
  - the warning that deleting the stack (`aws cloudformation delete-stack --stack-name elder-companion`) erases all data and stops all charges;
  - the shared-LiveKit-project warning.

  Also update the Security section: production sits behind a site password.
- [ ] **Step 5: Commit and push** (`docs: deployment guide`). Expected: the pipeline is green again.
