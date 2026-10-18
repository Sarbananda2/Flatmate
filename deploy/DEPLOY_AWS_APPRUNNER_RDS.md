# Production: AWS App Runner + Amazon RDS (no servers to manage)

Flatmate runs on managed AWS services in **ap-south-1 (Mumbai)**, in account
`277659481755`. There is no server to patch: App Runner runs the container and
gives it HTTPS, and RDS runs PostgreSQL with automatic backups.

```
GitHub push to main
  └─ CI (tests, type check, Docker build)
       └─ passes → "CD - Publish to AWS App Runner"
            └─ image → Amazon ECR flatmate-app:<commit> and :latest
                 └─ App Runner service "flatmate" deploys :latest
                      └─ (private VPC connector) → RDS "flatmate-db", PostgreSQL 16
```

> AWS App Runner stopped accepting new customers on 30 April 2026. This
> account has access; AWS keeps the service running but adds no features, and
> recommends Amazon ECS Express Mode for new work. The container needs no
> changes to move there if that is ever needed.

---

## What exists

| Resource | Name | Notes |
| --- | --- | --- |
| RDS PostgreSQL 16.15 | `flatmate-db` | db.t4g.micro, 20 GB gp3 growing to 100 GB, encrypted, deletion protection, 7 days of backups and point-in-time recovery. **Not publicly accessible.** |
| Security groups | `flatmate-db-sg`, `flatmate-apprunner-sg` | The database accepts port 5432 only from the App Runner connector's group. |
| App Runner VPC connector | `flatmate-vpc` | The app's only path to the database. |
| App Runner service | `flatmate` | 0.25 vCPU / 1 GB, 1–2 instances (`flatmate-small`), port 8787, health check `GET /health`. |
| ECR repository | `flatmate-app` | Scanned on push; keeps the 15 most recent images. |
| Secrets Manager | `flatmate/db/owner-password`, `flatmate/db/runtime-password` | Generated in AWS; never stored anywhere else. RDS keeps the master password in its own `rds!db-…` secret. |
| IAM roles | `flatmate-apprunner-ecr-access`, `flatmate-apprunner-instance`, `flatmate-github-ecr-push` | Pull from ECR; read the app's two passwords; push from GitHub's `main` only (OIDC — no AWS keys in GitHub). |

### The database roles

As on a local install, the app never uses the master user:

- **`aem`** — what the app serves requests as. Owns nothing, cannot change the schema.
- **`aem_owner`** — owns the schema; the app runs migrations as it when it starts.
- **`postgres`** (master) — used only by `manage.py bootstrap-roles` on the
  **first** start, to create the two roles, the `aem` database and the trigger
  guard. After that the service no longer receives the master password, and
  its instance role can no longer read it.

## Deploying

Push to `main`. When CI passes, the publish workflow builds the image and
pushes it to ECR, and App Runner deploys it within a few minutes, applying any
new migrations as it starts. Nothing else to do.

To redeploy by hand: **Actions → CD - Publish to AWS App Runner → Run workflow**,
or in the App Runner console, **flatmate → Deploy**.

**Rolling back:** in ECR, find the previous image (tagged with its commit), and
re-tag it `latest` (or run the publish workflow by hand on that commit); App
Runner deploys it. A rollback does not undo a migration: migrations only add.

## The first visit: create the admin account at once

Whoever opens a fresh installation first creates the **household admin**, and
signing up then closes. Open the app as soon as the first deployment is running
and create your account.

## Linking your domain

App Runner gives the service an address like `https://xxxx.ap-south-1.awsapprunner.com`.
To use your own:

1. App Runner console → **flatmate → Custom domains → Link domain**, enter e.g.
   `expenses.example.com` (leave **www** off unless you want it).
2. Add the records it shows to your DNS: one **CNAME** for the site itself, and
   the **CNAME**s that validate the certificate.
3. The status becomes **Active** once DNS has spread (minutes to a few hours);
   HTTPS is provided and renewed by AWS.

## Looking after it

- **Logs:** App Runner console → flatmate → **Logs**, or CloudWatch log groups
  `/aws/apprunner/flatmate/...`.
- **Backups:** RDS takes a daily snapshot and keeps 7 days, and can restore to
  any second within them (**RDS → flatmate-db → Actions → Restore to point in time**,
  which creates a new instance). Deletion protection stops the database being
  deleted by accident.
- **Health:** `https://<your address>/health` should say `"status":"ok"`, with
  the role `aem`, not privileged, and the trigger guard installed.
- **Cost:** roughly $25–30 a month — RDS about $18, App Runner $5–10 at a
  household's traffic, Secrets Manager about $1.

### Changing an app database password

1. Secrets Manager → `flatmate/db/owner-password` or `runtime-password` →
   **Retrieve secret value → Edit**, set a new random value.
2. Give the service the master secret again for one deployment, so
   `bootstrap-roles` can set the new password on the role: add the environment
   secret `AEM_DB_BOOTSTRAP_SECRET` = the RDS master secret's ARN, and let the
   instance role read it; deploy; then remove both again.
