# RAG Service — Server Setup Guide

This document covers setting up a fresh Ubuntu server for the `rag_service`
Frappe app. This is a **fresh install only** — there is no DB restore as
rag_service has no persistent student data; all configuration is seeded via
`server_setup.py`. Tested on Ubuntu 22.04 / GCP Compute Engine.

---

## Prerequisites

- Ubuntu 22.04 VM (GCP or equivalent)
- A bench user account (e.g. `rag-dev`) — do **not** run bench as root
- SSH access to the server
- Service account JSON files copied to `~/tap/` on the server:
  - `GCS Settings.json` — GCS service account credentials
  - `llm-settings-gemini-2.5-flash-lite.json` — Vertex AI service account credentials
- `~/rag.env` filled in from `rag.env.example`

---

## 1. Install System Dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-pip python3-venv redis-server \
    postgresql postgresql-contrib nginx supervisor \
    libpq-dev wkhtmltopdf cron
```

### Node.js (via nvm — must be Node 18)

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
source ~/.bashrc
nvm install 18
nvm use 18
nvm alias default 18
```

> **Important:** rag_service runs on Frappe v15 which requires Node 18+.
> This is the opposite of tap_lms which requires Node 16.

### Add bench to PATH

```bash
echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
```

### Install yarn

```bash
npm install -g yarn
```

### Make node visible to supervisor (runs as root)

```bash
sudo ln -sf ~/.nvm/versions/node/v18.*/bin/node /usr/local/bin/node
sudo ln -sf ~/.nvm/versions/node/v18.*/bin/npm /usr/local/bin/npm
```

---

## 2. Install bench

```bash
pip3 install frappe-bench
bench --version  # verify
```

---

## 3. Initialise the Bench

```bash
bench init frappe-bench \
    --frappe-branch version-15 \
    --frappe-path https://github.com/frappe/frappe.git
cd frappe-bench
```

> If `bench init` fails with `FileNotFoundError: /usr/bin/crontab`, install
> cron (`sudo apt install -y cron`), remove the partial bench directory
> (`rm -rf ~/frappe-bench`), and retry.

---

## 4. Get the App

```bash
bench get-app https://github.com/<org>/rag_service
```

---

## 5. PostgreSQL Setup

### Enable password authentication

```bash
sudo cat /etc/postgresql/*/main/pg_hba.conf | grep "local.*all.*all"
```

If it shows `peer`, change to `md5`:

```bash
sudo sed -i 's/local\s*all\s*all\s*peer/local   all             all                                     md5/' \
    /etc/postgresql/*/main/pg_hba.conf

sudo systemctl restart postgresql

# set postgres user password
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
```

---

## 6. Create the Site

```bash
bench new-site rag.dev \
    --db-type postgres \
    --db-host localhost
# enter postgres password when prompted
# set an admin password when prompted

bench --site rag.dev install-app rag_service
bench use rag.dev
```

---

## 7. Install Python Dependencies

`bench get-app` does not always install all Python dependencies. Install
them explicitly:

```bash
cd ~/frappe-bench
./env/bin/pip install -r apps/rag_service/requirements.txt
```

> `requirements.txt` includes `python-dotenv` which is needed by
> `server_setup.py`. If you get `ModuleNotFoundError: No module named 'pika'`
> or similar, run this step again.

---

## 8. Run Migrations

Start Redis first (supervisor isn't set up yet):

```bash
redis-server ~/frappe-bench/config/redis_cache.conf &
redis-server ~/frappe-bench/config/redis_queue.conf &

bench --site rag.dev migrate
```

---

## 9. Seed Configuration via server_setup.py

All rag_service configuration (RabbitMQ, RAG Settings, GCS, LLM) is seeded
via `server_setup.py`. This replaces the manual bootstrap steps done locally
via `bootstrap_lms.sh`.

### Prepare credentials files

```bash
# create the tap directory for credentials
mkdir -p ~/tap
chmod 700 ~/tap

# copy service account JSON files from your local machine
scp "~/tap/GCS Settings.json" \
    ~/tap/llm-settings-gemini-2.5-flash-lite.json \
    rag-dev@<server-ip>:~/tap/
```

### Create ~/rag.env

```bash
cp ~/frappe-bench/apps/rag_service/rag_service/scripts/rag.env.example ~/rag.env
chmod 600 ~/rag.env
nano ~/rag.env  # fill in all values
```

### Get Administrator API key from tap_lms server

The `RAG_API_KEY` and `RAG_API_SECRET` are the Frappe Administrator API
key/secret from the **tap_lms server** — rag_service uses these to
authenticate callbacks to tap_lms.

On the tap_lms server:
```bash
bench --site tap_lms.dev console
```
```python
frappe.db.get_value("User", "Administrator", ["api_key", "api_secret"])
```

If no API key exists, generate one:
```
Frappe Desk → Settings → Users → Administrator → API Access → Generate Keys
```

### Run the setup script

```bash
cd ~/frappe-bench/sites
../env/bin/python ../apps/rag_service/rag_service/scripts/server_setup.py
```

Expected output:
```
Loaded env from: /home/rag-dev/rag.env
=== RAG Service Server Setup — rag.dev ===
✓ RabbitMQ Settings saved
✓ RAG Settings saved (api_secret encrypted)
✓ GCS Settings saved
✓ LLM Settings saved (provider: Gemini)
=== Done. Restart consumer to apply ===
```

### Verify LLM Settings

```bash
bench --site rag.dev console
```
```python
# check record exists and is active
frappe.get_list("LLM Settings", filters={"is_active": 1},
    fields=["name", "provider", "model_name", "is_active"])

# set as default if not already
doc = frappe.get_doc("LLM Settings", {"provider": "Gemini"})
doc.is_default = 1
doc.save()
frappe.db.commit()
```

### Test LLM end-to-end

```python
import asyncio
from rag_service.core.llm_providers import create_llm_provider

settings = frappe.get_list("LLM Settings", filters={"is_active": 1},
    fields=["name"], limit=1)
doc = frappe.get_doc("LLM Settings", settings[0].name)

provider = create_llm_provider(
    provider=doc.provider,
    api_key="",
    model_name=doc.model_name,
    temperature=doc.temperature,
    max_tokens=100,
    settings=doc,
)

response, cost, _ = asyncio.run(provider.generate([
    {"role": "user", "content": "Say hello in one word."}
]))
print("Response:", response)
print("Cost:", cost)
```

---

## 10. Set Up Supervisor

```bash
bench setup supervisor
sudo ln -sf ~/frappe-bench/config/supervisor.conf \
    /etc/supervisor/conf.d/frappe-bench.conf
```

### Fix socketio — use full Node 18 path

```bash
# get exact node path
which node
# e.g. /home/rag-dev/.nvm/versions/node/v18.20.8/bin/node

sed -i 's|command=.*bench socketio|command=/home/rag-dev/.nvm/versions/node/v18.20.8/bin/node /home/rag-dev/frappe-bench/apps/frappe/socketio.js|' \
    ~/frappe-bench/config/supervisor.conf

# verify
grep -A4 "node-socketio\]" ~/frappe-bench/config/supervisor.conf
```

### Add RAG consumer to supervisor

Add the following to `~/frappe-bench/config/supervisor.conf`:

```ini
[program:frappe-bench-rag-consumer]
command=/home/rag-dev/frappe-bench/env/bin/python /home/rag-dev/frappe-bench/apps/rag_service/rag_service/scripts/console_consumer.py
directory=/home/rag-dev/frappe-bench/sites
environment=SITE_NAME="rag.dev",BENCH_SITES_PATH="/home/rag-dev/frappe-bench/sites"
user=rag-dev
autostart=true
autorestart=true
stdout_logfile=/home/rag-dev/frappe-bench/logs/rag-consumer.log
stderr_logfile=/home/rag-dev/frappe-bench/logs/rag-consumer.error.log
```

> **`BENCH_SITES_PATH` is required** — the script derives the sites path
> relative to its own location (4 levels up from `scripts/`). Setting it
> explicitly avoids any path resolution issues.

### Kill any manually started Redis instances

```bash
sudo pkill -f "redis-server.*frappe-bench"
```

### Start supervisor

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start all
sudo systemctl enable supervisor
sudo supervisorctl status
```

All processes should show `RUNNING`. Check the consumer log:

```bash
cat ~/frappe-bench/logs/rag-consumer.log
```

---

## 11. Set Up nginx

```bash
bench setup nginx
sudo rm /etc/nginx/sites-enabled/default

sudo ln -sf ~/frappe-bench/config/nginx.conf \
    /etc/nginx/conf.d/frappe-bench.conf

# add log_format (not included by default on Ubuntu)
sudo sed -i '/http {/a\\n\tlog_format main '"'"'$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for"'"'"';' \
    /etc/nginx/nginx.conf

# set server_name
sed -i 's|server_name ;|server_name rag.dev;|' \
    ~/frappe-bench/config/nginx.conf

# fix asset permissions
chmod o+x /home/rag-dev
chmod o+x /home/rag-dev/frappe-bench
chmod o+x /home/rag-dev/frappe-bench/sites
chmod -R o+rX /home/rag-dev/frappe-bench/sites/assets

sudo nginx -t
sudo systemctl enable nginx
sudo systemctl start nginx
```

---

## 12. Build Assets

```bash
bench build
```

---

## 13. GCP Firewall

```
GCP Console → Compute Engine → VM Instances → click instance → Edit →
Firewalls → check "Allow HTTP traffic"
```

---

## 14. Final Steps

```bash
bench --site rag.dev clear-cache
bench --site rag.dev clear-website-cache
sudo supervisorctl restart all
sudo systemctl restart nginx
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bench: command not found` | `~/.local/bin` not in PATH | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
| `FileNotFoundError: /usr/bin/crontab` during bench init | cron not installed | `sudo apt install -y cron`, remove partial bench, retry |
| `engine "node" is incompatible, Expected version ">=18"` | Node 16 installed instead of 18 | `nvm install 18 && nvm use 18 && nvm alias default 18` |
| `InvalidRemoteException: Invalid frappe path` | No outbound internet access | Check VM has external IP in GCP Console |
| `local all all peer` in pg_hba.conf | PostgreSQL not accepting password auth | Change to `md5` and restart postgresql |
| `ModuleNotFoundError: No module named 'pika'` | Python deps not installed | `./env/bin/pip install -r apps/rag_service/requirements.txt` |
| `IncorrectSitePath: rag.dev does not exist` | Site not created yet or wrong BENCH_SITES_PATH | Create site first; set `BENCH_SITES_PATH` in supervisor env |
| `Password not found for RAG Settings api_secret` | setup script not run or failed | Run `server_setup.py` |
| `MalformedError: missing fields client_email, token_uri` | Wrong JSON file used for credentials | Use GCP service account JSON, not Frappe doc export |
| `node-socketio BACKOFF` in supervisor | Node path not set correctly | Use exact nvm Node 18 path in supervisor.conf |
| `Response: <coroutine object...>` when testing LLM | Async method called without await | Use `asyncio.run(provider.generate([...]))` |
| LLM Settings not found | Queried as Single DocType | Use `frappe.get_list("LLM Settings", filters={"is_active": 1})` |
| Consumer exits immediately | Missing env vars or wrong BENCH_SITES_PATH | Check `rag-consumer.error.log`; verify `BENCH_SITES_PATH` |

---

## Notes

- rag_service uses **Frappe v15** and **Node 18** — opposite of tap_lms (v14, Node 16).
- There is no DB restore for rag_service — all config is seeded via `server_setup.py`.
- `server_setup.py` is safe to re-run — all steps are idempotent.
- The LLM `credentials_json` files from `~/tap/` are Frappe doc exports — `server_setup.py` automatically extracts the nested service account JSON from them.
- `is_default=1` should be set on the active LLM Settings record so provider selection is deterministic when multiple providers exist.
- After any code deploy: `sudo supervisorctl restart frappe-bench-workers: frappe-bench-rag-consumer`
- After any hooks.py change: `sudo supervisorctl restart all`
