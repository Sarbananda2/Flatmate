# Architectural Specification: CI/CD Pipeline & AWS Lightsail Production Deployment

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Implementer)  
**Status:** Approved for Implementation & Commit  
**Governing Principles:** Zero-Touch Continuous Deployment, Automated Quality Gates, Reliable Containerized Hosting, S3 Automated Disaster Recovery.  
**Files Added & Configured:**
- CI/CD Workflows:
  - `.github/workflows/ci.yml` (Automated backend tests, frontend typecheck/vitest, Docker build verification)
  - `.github/workflows/deploy.yml` (Continuous deployment to AWS Lightsail on push to `main`)
- Container & Production Configuration:
  - `Dockerfile` (Multi-stage Node 20 frontend build + Python 3.12 runner)
  - `.dockerignore`
  - `docker-compose.prod.yml` (PostgreSQL 16, AEM application, Caddy reverse proxy)
  - `Caddyfile` (Automated Let's Encrypt SSL/HTTPS)
  - `.env.production.example` (Template for production credentials and S3 bucket)
- Operational Scripts & Guides:
  - `deploy/aws-user-data.sh` (Lightsail / EC2 cloud-init script for Docker)
  - `deploy/backup_to_s3.sh` (Hot PostgreSQL backup & S3 synchronization script)
  - `deploy/DEPLOY_AWS_LIGHTSAIL.md` (End-to-end production deployment walkthrough)

---

## 1. Context & Purpose

The user confirmed the production deployment architecture for the Apartment Expense Manager:
1. **Target Environment:** AWS Lightsail ($5.00/mo Ubuntu 24.04 LTS instance with static IP).
2. **Reverse Proxy & TLS:** Caddy with automatic Let's Encrypt certificates.
3. **Database:** PostgreSQL 16 container with persistent SSD volume.
4. **Disaster Recovery:** Daily automated hot backup snapshot (`apartment-YYYYMMDD.dump`) synced to an Amazon S3 bucket.
5. **CI/CD:** Automated GitHub Actions pipeline running all test suites on PRs/pushes, and auto-deploying to Lightsail when merged to `main`.

---

## 2. CI/CD Architecture

### 2.1. Continuous Integration (`.github/workflows/ci.yml`)
Runs on every push to `main` and on pull requests:
- **`backend-test` job:**
  - Spins up a real `postgres:16-alpine` service container.
  - Provisions the `aem_owner` migration role and `aem` runtime role.
  - Executes `pytest tests legacy -q`.
- **`frontend-test` job:**
  - Sets up Node 20 with `npm ci`.
  - Runs `npm run typecheck` (`tsc --noEmit`).
  - Runs `npm test` (`vitest run`).
- **`docker-build` job:**
  - Validates that the multi-stage `Dockerfile` builds cleanly without pushing.

### 2.2. Continuous Deployment (`.github/workflows/deploy.yml`)
Runs automatically when code lands on `main` (after CI succeeds) or via manual trigger:
- Uses `appleboy/ssh-action` to connect to the Lightsail instance.
- Pulls `origin/main`.
- Rebuilds and launches the production containers via `docker compose -f docker-compose.prod.yml up -d --build`.
- Uses GitHub Secrets:
  - `LIGHTSAIL_HOST`: Public static IP of the instance.
  - `LIGHTSAIL_USER`: `ubuntu`.
  - `LIGHTSAIL_SSH_KEY`: The Lightsail instance SSH private key.

---

## 3. Implementation Tasks for Claude Code

1. **Verify Files & Script Permissions:**
   - Verify YAML syntax in `.github/workflows/ci.yml` and `.github/workflows/deploy.yml`.
   - Verify `Dockerfile`, `docker-compose.prod.yml`, and `Caddyfile`.
   - Ensure executable permissions on deployment scripts:
     ```bash
     chmod +x deploy/backup_to_s3.sh deploy/aws-user-data.sh
     ```

2. **Stage, Commit, and Push:**
   - Stage all deployment, CI/CD, and handoff files:
     ```bash
     git add .github/ Dockerfile .dockerignore docker-compose.prod.yml Caddyfile .env.production.example deploy/ docs/handoffs/
     ```
   - Commit:
     ```bash
     git commit -m "ci(deploy): add GitHub Actions CI/CD workflows and AWS Lightsail production stack"
     ```
   - Push to `origin/main` on `Sarbananda2/Flatmate`:
     ```bash
     git push origin main
     ```

3. **Streamline GitHub & AWS Linking:**
   - Remind the user to open the GitHub Repository Secrets page:
     👉 `https://github.com/Sarbananda2/Flatmate/settings/secrets/actions`
   - Prompt the user to enter the 3 repository secrets:
     1. `LIGHTSAIL_HOST`: Public Static IP from Lightsail (e.g. `13.x.x.x` or `54.x.x.x`).
     2. `LIGHTSAIL_USER`: `ubuntu`.
     3. `LIGHTSAIL_SSH_KEY`: The contents of the downloaded Lightsail `.pem` private key.
   - Once secrets are configured, verify the pipeline by checking the GitHub Actions tab on `Sarbananda2/Flatmate`.

