#!/usr/bin/env python
# rag_service/scripts/server_setup.py
#
# One-time setup script for a fresh rag_service server installation.
# Seeds all required DocType settings from environment variables.
#
# Usage:
#   1. Copy rag.env.example to rag.env and fill in values
#   2. Run from the bench sites directory:
#
#      cd ~/frappe-bench/sites
#      ../env/bin/python ../apps/rag_service/rag_service/scripts/server_setup.py
#
#   The script auto-loads ~/rag.env — override with ENV_FILE env var:
#      ENV_FILE=/path/to/custom.env ../env/bin/python .../server_setup.py
#
# Safe to re-run — all steps are idempotent.

import importlib
import os
import sys

from dotenv import load_dotenv

# Load .env file — consistent with how tap_plg loads config
env_file = os.getenv("ENV_FILE", os.path.expanduser("~/rag.env"))
if os.path.exists(env_file):
    load_dotenv(env_file)
    print(f"Loaded env from: {env_file}")
else:
    print(f"Warning: env file not found at {env_file} — using shell environment only")

# ── Bootstrap Frappe ──────────────────────────────────────────────────────────
import frappe
import frappe.utils.password as frappe_crypt

bench_sites_path = os.getenv(
    "BENCH_SITES_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "sites"),
)
bench_sites_path = os.path.abspath(bench_sites_path)
site_name = os.getenv("SITE_NAME", "rag.dev")

frappe.init(site=site_name, sites_path=bench_sites_path)
frappe.local.site = site_name
frappe.local.conf = frappe.get_site_config(site_name)
frappe.local.lang = "en"
frappe.connect()
frappe.set_user("Administrator")

# Force-register app modules
installed_apps = frappe.get_installed_apps()
frappe.local.app_modules = {}
for app in installed_apps:
    try:
        frappe.local.app_modules[app] = importlib.import_module(app)
    except ImportError:
        continue

print(f"\n=== RAG Service Server Setup ===")
print(f"    site : {site_name}")
print(f"    sites: {bench_sites_path}\n")


def require(var: str) -> str:
    """Get env var or exit with a clear error."""
    val = os.getenv(var)
    if not val:
        print(f"ERROR: required environment variable {var!r} is not set.")
        sys.exit(1)
    return val


def optional(var: str, default: str = "") -> str:
    return os.getenv(var, default)


# ── 1. RabbitMQ Settings ──────────────────────────────────────────────────────
print("── 1. RabbitMQ Settings")
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
print("    ✓ RabbitMQ Settings saved")

# ── 2. RAG Settings ───────────────────────────────────────────────────────────
print("── 2. RAG Settings")
tap_lms_base_url = require("TAP_LMS_BASE_URL")
api_key = require("RAG_API_KEY")
api_secret = require("RAG_API_SECRET")

rag = frappe.get_doc("RAG Settings", "RAG Settings")
rag.base_url = tap_lms_base_url
rag.assignment_context_endpoint = optional(
    "RAG_ASSIGNMENT_CONTEXT_ENDPOINT",
    "api/method/tap_lms.imgana.submission.get_assignment_context"
)
rag.student_context_endpoint = optional(
    "RAG_STUDENT_CONTEXT_ENDPOINT",
    "api/method/tap_lms.imgana.submission.get_student_details"
)
rag.enable_caching = optional("RAG_ENABLE_CACHING", "0")
rag.api_key = api_key
rag.save(ignore_permissions=True)
frappe.db.commit()
print("    ✓ RAG Settings saved")

# api_secret must be stored in Frappe's encrypted password vault
frappe_crypt.set_encrypted_password(
    "RAG Settings", "RAG Settings", api_secret, "api_secret"
)
frappe.db.commit()
print("    ✓ RAG Settings api_secret encrypted and stored in vault")

# ── 3. GCS Settings ───────────────────────────────────────────────────────────
print("── 3. GCS Settings")
frappe.db.set_value("GCS Settings", "GCS Settings", {
    "project_id":       optional("GCS_PROJECT_ID"),
    "credentials_json": optional("GCS_CREDENTIALS_JSON", "{}"),
})
frappe.db.commit()
print("    ✓ GCS Settings saved")

# ── 4. LLM Settings ───────────────────────────────────────────────────────────
print("── 4. LLM Settings")
llm_provider   = require("LLM_PROVIDER")
llm_model_name = require("LLM_MODEL_NAME")
llm_base_url   = require("LLM_BASE_URL")
llm_api_key    = require("LLM_API_KEY")

if not frappe.db.exists("LLM Settings", {"provider": llm_provider}):
    doc = frappe.new_doc("LLM Settings")
    doc.provider   = llm_provider
    doc.model_name = llm_model_name
    doc.base_url   = llm_base_url
    doc.api_key    = llm_api_key
    doc.is_active  = 1
    doc.insert()
    print(f"    ✓ LLM Settings created for provider: {llm_provider}")
else:
    doc = frappe.get_doc("LLM Settings", {"provider": llm_provider})
    doc.model_name = llm_model_name
    doc.base_url   = llm_base_url
    doc.api_key    = llm_api_key
    doc.is_active  = 1
    doc.save()
    print(f"    ✓ LLM Settings updated for provider: {llm_provider}")
frappe.db.commit()

# ── Done ──────────────────────────────────────────────────────────────────────
print("\n=== Setup complete. Restart the consumer to apply settings: ===")
print("    sudo supervisorctl restart frappe-bench-rag-consumer\n")

frappe.destroy()
