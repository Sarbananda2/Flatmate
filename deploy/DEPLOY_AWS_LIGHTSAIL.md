# Production Deployment Guide: AWS Lightsail & Docker

This guide puts the Apartment Expense Manager on an **AWS Lightsail** server with
HTTPS (Caddy and Let's Encrypt), a PostgreSQL database on the server's disk,
nightly backups to Amazon S3, and automatic deploys from GitHub Actions.

You need: an AWS account, a domain whose DNS you can edit, and admin access to
the GitHub repository `Sarbananda2/Flatmate`.

---

## 1. Create the Lightsail instance

1. Open the [AWS Lightsail console](https://lightsail.aws.amazon.com/) and click **Create instance**:
   - **Platform:** Linux/Unix
   - **Blueprint:** OS Only → **Ubuntu 24.04 LTS**
   - **Plan:** 1 GB RAM or more. (The image is built on the server, and the
     frontend build needs the memory; on the smallest plan, add swap — step 3A.)
   - **Name:** e.g. `aem-production`
2. **Networking** tab:
   - **Create static IP** and attach it to the instance. Note the address.
   - **IPv4 Firewall:** allow `SSH (22)`, `HTTP (80)` and `HTTPS (443)` only.
3. Download the instance's SSH key (**Account → SSH keys**); GitHub Actions uses
   it to deploy (step 7).

## 2. Point your domain at it

In your DNS provider, add an **A record**, e.g. `expenses` → the static IP.
If you use Cloudflare, leave it **DNS only** (grey cloud) so Let's Encrypt can
issue the certificate.

## 3. Prepare the server

```bash
ssh -i LightsailDefaultKey.pem ubuntu@YOUR_STATIC_IP
```

### A. Docker (and swap on a 512 MB–1 GB plan)
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile \
  && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
exit   # log out and back in so the docker group applies
```

### B. Let the server read the private repository
The repository is private, so the server needs its own read-only **deploy key**:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N "" -C "aem-production"
cat ~/.ssh/github_deploy.pub
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
EOF
```
On GitHub: **Flatmate → Settings → Deploy keys → Add deploy key**, paste the
public key, and leave **Allow write access** unticked.

### C. Clone and configure
```bash
git clone git@github.com:Sarbananda2/Flatmate.git ~/aem
cd ~/aem
cp .env.production.example .env
chmod 600 .env
nano .env
```
Set `DOMAIN`, and replace each of the three passwords with a generated one:
```bash
openssl rand -base64 24 | tr -d '/+='
```
The database roles are created with these passwords the **first** time the
stack starts. Changing them in `.env` later does not change the roles.

## 4. Launch, then create the admin account straight away

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps        # db (healthy), app, caddy: running
```

Open `https://expenses.yourdomain.com` **as soon as it answers**: you will see
**"Create your Household Admin Account"**. The first person to fill this in
becomes the household admin and signing up then closes, so do it before anyone
else can reach the site. Then go through the guided setup.

Every page and API route needs that login. The app serves requests as a
database role that cannot change the schema, and the session cookie is HTTPS-only.

## 5. Nightly backups to S3

1. In S3, create a **private** bucket, e.g. `my-household-expenses-backups`.
2. In IAM, create a user (or a Lightsail-attached role) that may only
   `s3:PutObject` to that bucket, and create an access key for it.
3. On the server:
   ```bash
   sudo apt-get install -y awscli
   aws configure          # that access key, and the bucket's region
   ```
4. Put the bucket name in `.env` as `AEM_S3_BACKUP_BUCKET`, then test:
   ```bash
   ~/aem/deploy/backup_to_s3.sh
   ```
5. Schedule it nightly at 03:00 (`crontab -e`):
   ```
   0 3 * * * /home/ubuntu/aem/deploy/backup_to_s3.sh >> /home/ubuntu/aem-backup.log 2>&1
   ```
The script reads its settings from `.env` itself, so cron needs nothing more.
It keeps 30 days of copies on the server. If a bucket is set but the upload
cannot run, it exits with an error rather than skipping it quietly.

To restore a backup into an empty database, see `docs/OPERATIONS.md`.

## 6. Updating by hand

```bash
cd ~/aem
git fetch origin main && git checkout --detach origin/main
docker compose -f docker-compose.prod.yml up -d --build
```
The app restarts (a few seconds of downtime) and applies any new database
migrations as it starts. Data lives in the `postgres_data` volume and survives.

## 7. Automatic deploys from GitHub Actions

Two workflows live in `.github/workflows/`:

- **`ci.yml`** runs on every push to `main` and every pull request: the backend
  tests against PostgreSQL, the frontend type check and tests, and a Docker build.
- **`deploy.yml`** runs **only after CI passes** on `main` (or by hand from the
  Actions tab). It connects to the server over SSH, checks out exactly the
  commit CI tested, rebuilds, and waits for the app to answer.

Until the server is set up, the deploy workflow just notes that there is
nothing to deploy to. To switch it on, add these under
**Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
| --- | --- |
| `LIGHTSAIL_HOST` | the static IP |
| `LIGHTSAIL_USER` | `ubuntu` |
| `LIGHTSAIL_SSH_KEY` | the full contents of the Lightsail `.pem` key |

From then on, every push to `main` that passes CI is deployed.
