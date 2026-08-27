import os

import frappe


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def seed_optional_records():
    """Seed optional local LLM records from environment variables.

    Single DocTypes are set from the shell script. These regular DocTypes need
    insert/update logic and are only useful when credentials are present.
    """
    llm_model_name = os.getenv("RAG_LLM_MODEL_NAME")
    llm_api_key = os.getenv("RAG_LLM_API_KEY")
    gemini_model_name = os.getenv("RAG_GEMINI_MODEL_NAME")
    gemini_project_id = os.getenv("RAG_GEMINI_PROJECT_ID")

    if llm_model_name and llm_api_key:
        name = f"Local {os.getenv('RAG_LLM_PROVIDER', 'Gemini')} {llm_model_name}"
        if frappe.db.exists("LLM Settings", name):
            doc = frappe.get_doc("LLM Settings", name)
        else:
            doc = frappe.new_doc("LLM Settings")
            doc.name = name

        doc.provider = os.getenv("RAG_LLM_PROVIDER", "Gemini")
        doc.model_name = llm_model_name
        doc.api_key = llm_api_key
        doc.api_secret = os.getenv("RAG_LLM_API_SECRET", "")
        doc.temperature = _as_float(os.getenv("RAG_LLM_TEMPERATURE"), 0)
        doc.max_tokens = _as_int(os.getenv("RAG_LLM_MAX_TOKENS"), 2000)
        doc.is_active = 1
        doc.is_default = 1
        doc.save(ignore_permissions=True)

    if gemini_model_name and gemini_project_id:
        name = f"Local Gemini {gemini_model_name}"
        if frappe.db.exists("Gemini Settings", name):
            doc = frappe.get_doc("Gemini Settings", name)
        else:
            doc = frappe.new_doc("Gemini Settings")
            doc.name = name

        doc.model_name = gemini_model_name
        doc.project_id = gemini_project_id
        doc.location = os.getenv("RAG_GEMINI_LOCATION", "us-central1")
        doc.credentials_json = os.getenv("RAG_GEMINI_CREDENTIALS_JSON", "{}")
        doc.temperature = _as_float(os.getenv("RAG_LLM_TEMPERATURE"), 0)
        doc.max_tokens = _as_int(os.getenv("RAG_LLM_MAX_TOKENS"), 2000)
        doc.is_active = 1
        doc.is_default = 1
        doc.save(ignore_permissions=True)

    frappe.db.commit()
