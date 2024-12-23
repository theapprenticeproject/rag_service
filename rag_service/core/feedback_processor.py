# rag_service/rag_service/core/feedback_processor.py

import frappe
import json
from datetime import datetime
from typing import Dict, Optional
from ..utils.queue_manager import QueueManager

class FeedbackProcessor:
    def __init__(self):
        self.queue_manager = QueueManager()

    async def process_feedback(self, request_id: str, feedback: Dict) -> None:
        """Process and store feedback in Feedback Request DocType"""
        try:
            print(f"\n=== Processing Feedback for Request: {request_id} ===")
            
            # Get the feedback request document
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            print(f"Found Feedback Request: {feedback_request.name}")
            
            # Format feedback for display
            formatted_feedback = self.format_feedback_for_display(feedback)
            
            print("\nUpdating Feedback Request fields...")
            # Update document fields using db_set
            feedback_request.db_set('status', 'Completed', update_modified=True)
            feedback_request.db_set('generated_feedback', json.dumps(feedback, indent=2), update_modified=True)
            feedback_request.db_set('feedback_summary', formatted_feedback, update_modified=True)
            feedback_request.db_set('completed_at', datetime.now(), update_modified=True)
            
            # Get and set template and model info
            llm_settings = frappe.get_list(
                "LLM Settings",
                filters={"is_active": 1},
                limit=1
            )
            if llm_settings:
                feedback_request.db_set('model_used', llm_settings[0].name, update_modified=True)

            template = frappe.get_list(
                "Prompt Template",
                filters={
                    "assignment_type": "visual_arts",
                    "is_active": 1
                },
                order_by="version desc",
                limit=1
            )
            if template:
                feedback_request.db_set('template_used', template[0].name, update_modified=True)
            
            # Commit changes
            frappe.db.commit()
            
            # Verify the update
            updated_doc = frappe.get_doc("Feedback Request", request_id)
            print("\nVerification after update:")
            print(f"Status: {updated_doc.status}")
            print(f"Has Generated Feedback: {bool(updated_doc.generated_feedback)}")
            print(f"Has Feedback Summary: {bool(updated_doc.feedback_summary)}")
            
            # Prepare and send message to TAP LMS
            message = {
                "submission_id": feedback_request.submission_id,
                "student_id": feedback_request.student_id,
                "assignment_id": feedback_request.assignment_id,
                "feedback": feedback,
                "summary": formatted_feedback,
                "generated_at": feedback_request.completed_at.isoformat(),
                "plagiarism_score": feedback_request.plagiarism_score,
                "similar_sources": json.loads(feedback_request.similar_sources or '[]')
            }
            
            # Send to TAP LMS queue
            self.queue_manager.send_feedback_to_tap(message)
            
            print(f"\nFeedback processed and sent for request: {request_id}")
            
        except Exception as e:
            error_msg = f"Error processing feedback: {str(e)}"
            print(f"\nError: {error_msg}")
            
            try:
                if 'feedback_request' in locals():
                    feedback_request.db_set('status', 'Failed', update_modified=True)
                    feedback_request.db_set('error_log', error_msg, update_modified=True)
                    frappe.db.commit()
            except Exception as save_error:
                print(f"Error saving failure status: {str(save_error)}")
                
            frappe.log_error(error_msg, "Feedback Processing Error")
            raise

    def format_feedback_for_display(self, feedback: Dict) -> str:
        """Format feedback for human-readable display"""
        try:
            formatted = []
            
            # Overall feedback
            formatted.append("Overall Feedback:")
            formatted.append(feedback["overall_feedback"])
            
            # Strengths
            formatted.append("\nStrengths:")
            for strength in feedback["strengths"]:
                formatted.append(f"- {strength}")
            
            # Areas for improvement
            formatted.append("\nAreas for Improvement:")
            for area in feedback["areas_for_improvement"]:
                formatted.append(f"- {area}")
            
            # Learning objectives feedback
            formatted.append("\nLearning Objectives Feedback:")
            for obj in feedback["learning_objectives_feedback"]:
                formatted.append(f"- {obj}")
            
            # Grade and encouragement
            formatted.append(f"\nGrade Recommendation: {feedback['grade_recommendation']}")
            formatted.append(f"\nEncouragement: {feedback['encouragement']}")
            
            return "\n".join(formatted)
            
        except Exception as e:
            error_msg = f"Error formatting feedback: {str(e)}"
            print(f"\nError: {error_msg}")
            return "Error formatting feedback"
