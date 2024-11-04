import frappe
import json

def get_student_context(student_id):
    try:
        return frappe.get_doc("Student Context", {"student_id": student_id})
    except frappe.DoesNotExistError:
        frappe.log_error(f"Student Context not found for student_id: {student_id}")
        return None

def get_assignment_context(assignment_id):
    try:
        return frappe.get_doc("Assignment Context", {"assignment_id": assignment_id})
    except frappe.DoesNotExistError:
        frappe.log_error(f"Assignment Context not found for assignment_id: {assignment_id}")
        return None

def generate_feedback(submission_data):
    """
    Initial basic feedback generation
    """
    try:
        # Log the received data
        frappe.logger().info(f"Generating feedback for submission: {submission_data}")
        
        # Extract basic information
        student_id = submission_data.get("student_id")
        assignment_id = submission_data.get("assignment_id")
        plagiarism_score = submission_data.get("plagiarism_score", 0)
        
        # Get contexts
        student_context = get_student_context(student_id)
        assignment_context = get_assignment_context(assignment_id)
        
        # Generate basic feedback
        feedback = {
            "status": "completed",
            "plagiarism_assessment": {
                "score": plagiarism_score,
                "flag": "high" if plagiarism_score > 0.8 else "low"
            },
            "feedback_text": "Submission received and processed.",
            "timestamp": frappe.utils.now_datetime()
        }
        
        return feedback
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error in generate_feedback")
        return {
            "status": "error",
            "error_message": str(e),
            "timestamp": frappe.utils.now_datetime()
        }
