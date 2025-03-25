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

            # Get assignment type from Assignment Context
            assignment_type = None
            assignment_context = frappe.get_list(
                "Assignment Context", 
                filters={"assignment_id": feedback_request.assignment_id},
                fields=["assignment_type"],
                limit=1
            )
            
            if assignment_context:
                assignment_type = assignment_context[0].assignment_type
                print(f"Assignment type found: {assignment_type}")
            
                # Get matching template for this assignment type
                template = frappe.get_list(
                    "Prompt Template",
                    filters={
                        "assignment_type": assignment_type,
                        "is_active": 1
                    },
                    order_by="version desc",
                    limit=1
                )
                
                if template:
                    feedback_request.db_set('template_used', template[0].name, update_modified=True)
                    print(f"Using template: {template[0].name}")
                else:
                    # Try to find a generic template if no specific one was found
                    generic_template = frappe.get_list(
                        "Prompt Template",
                        filters={"is_active": 1},
                        order_by="version desc",
                        limit=1
                    )
                    if generic_template:
                        feedback_request.db_set('template_used', generic_template[0].name, update_modified=True)
                        print(f"Using generic template: {generic_template[0].name}")
            
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
                "generated_at": feedback_request.completed_at.isoformat() if feedback_request.completed_at else datetime.now().isoformat(),
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
                if 'feedback_request' in locals() and feedback_request:
                    feedback_request.db_set('status', 'Failed', update_modified=True)
                    feedback_request.db_set('error_log', error_msg, update_modified=True)
                    frappe.db.commit()
                    print(f"Request {request_id} marked as failed")
            except Exception as save_error:
                print(f"Error saving failure status: {str(save_error)}")
                
            frappe.log_error(error_msg, "Feedback Processing Error")
            raise

    def format_feedback_for_display(self, feedback: Dict) -> str:
        """Format feedback for human-readable display"""
        try:
            formatted = []
            
            # Standard fields
            if "overall_feedback" in feedback:
                formatted.append("Overall Feedback:")
                formatted.append(feedback["overall_feedback"])
            
            if "strengths" in feedback:
                formatted.append("\nStrengths:")
                for strength in feedback["strengths"]:
                    formatted.append(f"- {strength}")
                    
            if "areas_for_improvement" in feedback:
                formatted.append("\nAreas for Improvement:")
                for area in feedback["areas_for_improvement"]:
                    formatted.append(f"- {area}")
                    
            if "learning_objectives_feedback" in feedback:
                formatted.append("\nLearning Objectives Feedback:")
                for obj in feedback["learning_objectives_feedback"]:
                    formatted.append(f"- {obj}")
                    
            if "grade_recommendation" in feedback:
                formatted.append(f"\nGrade Recommendation: {feedback['grade_recommendation']}")
                
            if "encouragement" in feedback:
                formatted.append(f"\nEncouragement: {feedback['encouragement']}")
            
            # Include any additional fields not in the standard format
            standard_fields = ["overall_feedback", "strengths", "areas_for_improvement", 
                              "learning_objectives_feedback", "grade_recommendation", 
                              "encouragement", "detected_type", "error"]
            
            # Process any custom fields in the feedback
            for key, value in feedback.items():
                if key not in standard_fields:
                    formatted.append(f"\n{key.replace('_', ' ').title()}:")
                    if isinstance(value, list):
                        for item in value:
                            formatted.append(f"- {item}")
                    else:
                        formatted.append(str(value))
            
            return "\n".join(formatted)
            
        except Exception as e:
            error_msg = f"Error formatting feedback: {str(e)}"
            print(f"\nError: {error_msg}")
            return "Error formatting feedback for display. Please check the JSON feedback data."
