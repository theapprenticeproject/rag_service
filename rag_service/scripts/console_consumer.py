#!/usr/bin/env python
# ── STEP 1: INJECT RUNTIME PATHS BEFORE ANY OTHER CODES ──────────────────────
import sys
import os

py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"

isolated_paths = [
    "/workspace/rag_service",
    f"/home/frappe/rag_venv/lib/{py_ver}/site-packages"
]

for path in isolated_paths:
    if path not in sys.path:
        sys.path.insert(0, path)

# ── STEP 2: STABLE STANDALONE FRAPPE BOOTSTRAP ──────────────────────────────
import frappe
import importlib

# 1. Ensure global log folder is ready
os.makedirs("/home/frappe/logs", exist_ok=True)

# 2. Map system site configurations
bench_sites_path = "/home/frappe/frappe-bench/sites"
site_name = os.getenv("SITE_NAME", "tap_lms.localhost")

frappe.init(site=site_name, sites_path=bench_sites_path)

# 3. Create the site-specific log folder if it doesn't exist
try:
    sitelog_dir = os.path.dirname(frappe.utils.logger.get_log_filename("database", site_name))
    os.makedirs(sitelog_dir, exist_ok=True)
except Exception:
    os.makedirs(f"{bench_sites_path}/{site_name}/logs", exist_ok=True)

# 4. Populate site context flags manually before connecting
frappe.local.site = site_name
frappe.local.conf = frappe.get_site_config(site_name)
frappe.local.lang = "en"

# 5. Connect to the underlying database pool
frappe.connect()

# 6. FORCE-REGISTER APP MODULES (Fixes the RAG Settings Core Fallback Error)
installed_apps = frappe.get_installed_apps()
frappe.local.app_modules = {}
for app in installed_apps:
    try:
        frappe.local.app_modules[app] = importlib.import_module(app)
    except ImportError:
        continue

# ── STEP 3: RUN THE QUEUE LISTENER ──────────────────────────────────────────
from rag_service.utils.rabbitmq_consumer import RabbitMQConsumer

def run():
    """Main worker entry point"""
    consumer = RabbitMQConsumer(debug=True)
    if consumer.test_connection():
        print("Starting RabbitMQ consumer...")
        consumer.start_consuming()

if __name__ == "__main__":
    run()
