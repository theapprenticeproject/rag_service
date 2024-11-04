# File: ~/frappe-bench/apps/rag_service/rag_service/api/test_api.py

@frappe.whitelist(allow_guest=False)
def generate_submission_feedback():
    """Generate feedback for a submission"""
    try:
        data = frappe.request.get_json()
        
        if not data:
            frappe.throw("No data provided")
            
        content = data.get("content")
        submission_id = data.get("submission_id") or f"submission_{now()}"
        
        if not content:
            frappe.throw("Content is required")
            
        result = generate_feedback(submission_id, content)
        
        return {
            "status": "success",
            "submission_id": submission_id,
            "feedback": result["feedback"],
            "metadata": result["metadata"],
            "similar_contents": result["similar_contents"]
        }
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Feedback Generation Error")
        return {
            "status": "error",
            "message": str(e)
        }
