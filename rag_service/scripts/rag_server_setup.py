#!/usr/bin/env python
# rag_service/scripts/server_setup.py
#
# One-time setup script for a fresh rag_service server installation.
# Seeds all required DocType settings from a .env file.
#
# Usage:
#   1. Copy rag.env.example to ~/rag.env and fill in values
#   2. Place service account JSON files on the server (paths go in .env file)
#   3. Run:
#      cd ~/frappe-bench/sites
#      SITE_NAME=rag.dev ../env/bin/python ../apps/rag_service/rag_service/scripts/server_setup.py
#
# Safe to re-run — all steps are idempotent.

import importlib
import json
import os

from dotenv import load_dotenv
import frappe
import frappe.utils.password as frappe_crypt

# ── Load .env ─────────────────────────────────────────────────────────────────
env_file = os.getenv("ENV_FILE", os.path.expanduser("~/.env"))
if os.path.exists(env_file):
    load_dotenv(env_file)
    print(f"Loaded env from: {env_file}")
else:
    print(f"Warning: {env_file} not found — using shell environment only")


def require(var):
    val = os.getenv(var)
    if not val:
        print(f"ERROR: required variable {var!r} is not set in {env_file}")
        raise SystemExit(1)
    return val


def optional(var, default=""):
    return os.getenv(var, default)


def read_json_file(var):
    """
    Read a JSON file whose path is stored in the given env var.
    Handles two formats:
    - Raw GCP service account JSON (has 'type', 'client_email' at top level)
    - Frappe doc export (has 'credentials_json' field containing the service account JSON)
    """
    path = os.path.expanduser(require(var))
    if not os.path.exists(path):
        print(f"ERROR: file not found: {path!r} (from {var})")
        raise SystemExit(1)
    with open(path) as f:
        data = json.load(f)
    # if it's a Frappe doc export, extract the nested credentials_json
    if "credentials_json" in data and "type" not in data:
        inner = data["credentials_json"]
        data = json.loads(inner) if isinstance(inner, str) else inner
    return json.dumps(data)


# ── Bootstrap Frappe ──────────────────────────────────────────────────────────
bench_sites_path = os.path.abspath(
    os.getenv("BENCH_SITES_PATH",
              os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "sites"))
)
site_name = optional("SITE_NAME", "rag.dev")

frappe.init(site=site_name, sites_path=bench_sites_path)
frappe.local.site = site_name
frappe.local.conf = frappe.get_site_config(site_name)
frappe.local.lang = "en"
frappe.connect()
frappe.set_user("Administrator")

for app in frappe.get_installed_apps():
    try:
        frappe.local.app_modules = getattr(frappe.local, "app_modules", {})
        frappe.local.app_modules[app] = importlib.import_module(app)
    except ImportError:
        continue

print(f"\n=== RAG Service Server Setup — {site_name} ===\n")

# ── 1. RabbitMQ Settings ──────────────────────────────────────────────────────
frappe.db.set_value("RabbitMQ Settings", "RabbitMQ Settings", {
    "host":                     require("RABBITMQ_HOST"),
    "port":                     optional("RABBITMQ_PORT", "5672"),
    "virtual_host":             optional("RABBITMQ_VIRTUAL_HOST", "/"),
    "username":                 require("RABBITMQ_USERNAME"),
    "password":                 require("RABBITMQ_PASSWORD"),
    "plagiarism_results_queue": require("RABBITMQ_PLAGIARISM_RESULTS_QUEUE"),
    "feedback_results_queue":   optional("RABBITMQ_FEEDBACK_RESULTS_QUEUE"),
})
frappe.db.commit()
print("✓ RabbitMQ Settings saved")

# ── 2. RAG Settings ───────────────────────────────────────────────────────────
rag_doc = frappe.get_doc("RAG Settings", "RAG Settings")
rag_doc.base_url = require("TAP_LMS_BASE_URL")
rag_doc.assignment_context_endpoint = optional(
    "RAG_ASSIGNMENT_CONTEXT_ENDPOINT",
    "api/method/tap_lms.imgana.submission.get_assignment_context"
)
rag_doc.student_context_endpoint = optional(
    "RAG_STUDENT_CONTEXT_ENDPOINT",
    "api/method/tap_lms.imgana.submission.get_student_details"
)
rag_doc.enable_caching = optional("RAG_ENABLE_CACHING", "0")
rag_doc.api_key = require("RAG_API_KEY")
rag_doc.save(ignore_permissions=True)
frappe.db.commit()
frappe_crypt.set_encrypted_password(
    "RAG Settings", "RAG Settings", require("RAG_API_SECRET"), "api_secret"
)
frappe.db.commit()
print("✓ RAG Settings saved (api_secret encrypted)")

# ── 3. GCS Settings ───────────────────────────────────────────────────────────
frappe.db.set_value("GCS Settings", "GCS Settings", {
    "project_id":       optional("GCS_PROJECT_ID"),
    "credentials_json": read_json_file("GCS_CREDENTIALS_JSON_FILE"),
})
frappe.db.commit()
print("✓ GCS Settings saved")

# ── 4. LLM Settings ───────────────────────────────────────────────────────────
llm_provider = require("LLM_PROVIDER")
llm_values = {
    "provider":          llm_provider,
    "model_name":        require("LLM_MODEL_NAME"),
    "temperature":       float(optional("LLM_TEMPERATURE", "1")),
    "max_tokens":        int(optional("LLM_MAX_TOKENS", "1500")),
    "location":          optional("LLM_LOCATION"),
    "project_id":        optional("LLM_PROJECT_ID"),
    "credentials_json":  read_json_file("LLM_CREDENTIALS_JSON_FILE"),
    "is_active":         1,
}

if frappe.db.exists("LLM Settings", {"provider": llm_provider}):
    llm_doc = frappe.get_doc("LLM Settings", {"provider": llm_provider})
else:
    llm_doc = frappe.new_doc("LLM Settings")
for k, v in llm_values.items():
    setattr(llm_doc, k, v)
llm_doc.save(ignore_permissions=True)
frappe.db.commit()
print(f"✓ LLM Settings saved (provider: {llm_provider})")

print("\n=== Done. Restart consumer to apply: ===")
print("    sudo supervisorctl restart frappe-bench-rag-consumer\n")

frappe.destroy()
