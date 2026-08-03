#!/usr/bin/env python
# RAG service queue consumer — bootstraps Frappe context then starts consuming
# from the plagiarism_results RabbitMQ queue.
#
# Usage (from any directory):
#   SITE_NAME=rag.dev python rag_service/scripts/console_consumer.py
#
# Or via module invocation (as used in entrypoint.sh):
#   SITE_NAME=rag.dev python -c "import rag_service.scripts.console_consumer as cc; cc.run()"
#
# Or via supervisor (see docs/server_setup.md):
#   environment=SITE_NAME="rag.dev"
#   directory=/home/rag-dev/frappe-bench/sites
#   command=.../env/bin/python .../rag_service/scripts/console_consumer.py

# ── STEP 1: STABLE STANDALONE FRAPPE BOOTSTRAP ───────────────────────────────
import importlib
import os
import sys

import frappe

# Bench sites path — explicit so the script works from any working directory,
# not just when invoked from the sites/ folder.
# In Docker the path is /home/frappe/frappe-bench/sites — set BENCH_SITES_PATH
# to override. On the server, derived relative to this script's location.
bench_sites_path = os.getenv(
    "BENCH_SITES_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "sites"),
)
bench_sites_path = os.path.abspath(bench_sites_path)

site_name = os.getenv("SITE_NAME", "rag.localhost")

# 1. Init Frappe with explicit sites_path so it resolves site_config.json
#    regardless of the current working directory.
frappe.init(site=site_name, sites_path=bench_sites_path)

# 2. Ensure log directories exist.
os.makedirs(os.path.join(bench_sites_path, site_name, "logs"), exist_ok=True)

# 3. Populate site context flags manually before connecting — prevents silent
#    failures in Frappe internals that read frappe.local.conf before a full
#    request context is established.
frappe.local.site = site_name
frappe.local.conf = frappe.get_site_config(site_name)
frappe.local.lang = "en"

# 4. Connect to the database pool.
frappe.connect()

# 5. Force-register installed app modules so DocType lookups work correctly
#    and custom app imports don't fail with ImportError.
installed_apps = frappe.get_installed_apps()
frappe.local.app_modules = {}
for app in installed_apps:
    try:
        frappe.local.app_modules[app] = importlib.import_module(app)
    except ImportError:
        continue


# ── STEP 2: RUN THE QUEUE LISTENER ───────────────────────────────────────────
from rag_service.utils.rabbitmq_consumer import RabbitMQConsumer


def run():
    """
    Main entry point — can be called as a module or run directly as a script.

    Entrypoint (module):  python -c "import rag_service.scripts.console_consumer as cc; cc.run()"
    Entrypoint (script):  python rag_service/scripts/console_consumer.py
    Supervisor:           command=.../env/bin/python .../rag_service/scripts/console_consumer.py
    """
    print("\n=== Starting RAG Service Consumer ===")
    print(f"    site : {site_name}")
    print(f"    sites: {bench_sites_path}\n")

    consumer = RabbitMQConsumer(debug=True)
    if consumer.test_connection():
        consumer.start_consuming()
    else:
        print("RabbitMQ connection test failed — consumer not started.")
        sys.exit(1)


if __name__ == "__main__":
    run()
