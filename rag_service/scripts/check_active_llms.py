#!/usr/bin/env python

import sys
import os

# Add frappe to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'apps'))

def check_active_llms():
    """Check all active LLM settings and which one would be selected"""
    
    # Get all active settings
    all_active = frappe.get_list(
        "LLM Settings",
        filters={"is_active": 1},
        fields=["name", "provider", "model_name", "modified", "creation"],
        order_by="modified desc"
    )
    
    print(f"\n=== Active LLM Settings ({len(all_active)} found) ===\n")
    
    for i, setting in enumerate(all_active):
        status = "✓ WOULD BE SELECTED" if i == 0 else "  Would be ignored"
        print(f"{status}: {setting.provider} - {setting.model_name}")
        print(f"   Name: {setting.name}")
        print(f"   Modified: {setting.modified}")
        print(f"   Created: {setting.creation}")
        print()
    
    if len(all_active) > 1:
        print("⚠️  WARNING: Multiple active LLM settings found!")
        print("   Only the first one will be used.")
        print("   Please deactivate all but one to avoid confusion.")
    elif len(all_active) == 0:
        print("❌ No active LLM settings found!")
        print("   Please activate at least one LLM setting.")
    
    # Show what setup_llm would select
    selected = frappe.get_list(
        "LLM Settings",
        filters={"is_active": 1},
        limit=1,
        order_by="modified desc"
    )
    
    if selected:
        settings = frappe.get_doc("LLM Settings", selected[0].name)
        print(f"\n✓ The system will use: {settings.provider} - {settings.model_name}")
        print(f"   Name: {settings.name}")

if __name__ == "__main__":
    import frappe
    
    # Use the site name from command line or default
    site_name = sys.argv[1] if len(sys.argv) > 1 else 'rag-dev.localhost'
    
    try:
        frappe.init(site=site_name)
        frappe.connect()
        check_active_llms()
    except Exception as e:
        print(f"Error: {str(e)}")
        print(f"Make sure the site '{site_name}' exists")
    finally:
        if frappe and frappe.db:
            frappe.destroy()
