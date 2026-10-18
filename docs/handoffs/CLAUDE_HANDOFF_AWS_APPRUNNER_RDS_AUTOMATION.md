# Architectural Specification: AWS App Runner & Amazon RDS Zero-Server Deployment

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Implementer)  
**Status:** Approved for Execution  
**Governing Principles:** Zero Server Management, Decoupled Cloud Storage, Pure Git-Driven Continuous Delivery.  
**Files Added & Configured:**
- `deploy/DEPLOY_AWS_APPRUNNER_RDS.md` (Step-by-step console guide)
- `deploy/setup_aws_apprunner_rds.sh` (Automated AWS CLI provisioning script)
- `Dockerfile` (Multi-stage build compatible with AWS App Runner)

---

## 1. Goal & Requirements

The user specified:
> *"i don't want to do server management. my priorirty is to build the app. option 2 works for me."*

Instead of managing a Linux VPS with Docker, the application will run entirely on **AWS Managed Services**:
1. **Compute:** **AWS App Runner** (Directly connected to `Sarbananda2/Flatmate` branch `main`, building via Dockerfile, auto-deploying on every push, zero OS maintenance).
2. **Storage:** **Amazon RDS for PostgreSQL 16** (Managed database with automated daily snapshots, point-in-time recovery, and 20 GB gp3 auto-scaling storage).

---

## 2. Tasks for Claude Code

### Task 1: Commit and Push Deployment Scripts
1. Stage and commit the new App Runner and RDS deployment files:
   ```bash
   git add deploy/DEPLOY_AWS_APPRUNNER_RDS.md deploy/setup_aws_apprunner_rds.sh docs/handoffs/CLAUDE_HANDOFF_AWS_APPRUNNER_RDS_AUTOMATION.md
   git commit -m "ci(deploy): add AWS App Runner and Amazon RDS zero-server deployment automation"
   git push origin main
   ```

### Task 2: Provision Amazon RDS PostgreSQL
1. Verify if the AWS CLI is installed on the user's system (`aws --version`).
2. If installed, run `./deploy/setup_aws_apprunner_rds.sh` to:
   - Check AWS credentials (`aws sts get-caller-identity`).
   - Create the `flatmate-db` PostgreSQL 16 instance.
   - Authorize the security group for port 5432.
   - Capture the resulting `RDS_ENDPOINT`.
3. If the user prefers the web console, guide them through the 3-minute setup in `deploy/DEPLOY_AWS_APPRUNNER_RDS.md`.

### Task 3: Launch AWS App Runner
1. In the AWS App Runner console, create a new service:
   - Source: **Source code repository** $\rightarrow$ `Sarbananda2/Flatmate`, branch `main`.
   - Deployment trigger: **Automatic**.
   - Build runtime: **Dockerfile**.
   - Port: `8787`.
   - Environment variables:
     - `AEM_DB_HOST`: `<RDS_ENDPOINT>`
     - `AEM_DB_PORT`: `5432`
     - `AEM_DB_NAME`: `aem`
     - `AEM_DB_USER`: `postgres`
     - `AEM_DB_PASSWORD`: `<MASTER_PASSWORD>`
     - `AEM_DB_MIGRATION_USER`: `postgres`
     - `AEM_DB_MIGRATION_PASSWORD`: `<MASTER_PASSWORD>`
   - Health check: HTTP `/health` on port `8787`.

### Task 4: Custom Domain Verification
1. Map the user's custom domain in App Runner $\rightarrow$ **Custom domains**.
2. Provide the CNAME records to add in their DNS manager.
3. Test the live site: verify that navigating to the domain presents the **"Create your Household Admin Account"** screen.

