# rag_service/rag_service/core/feedback_processor.py

import frappe
import json
from datetime import datetime
from typing import Dict, Optional
from ..utils.queue_manager import QueueManager

class FeedbackProcessor:
    def __init__(self):
        self.queue_manager = QueueManager()

    async def process_feedback(self, request_id: str, feedback: Dict, model_used: str, template_used: str) -> None:
        """Process and store feedback in Feedback Request DocType"""
        try:
            print(f"\n=== Processing Feedback for Request: {request_id} ===")

            # Get the feedback request document
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            print(f"Found Feedback Request: {feedback_request.name}")

            print("\nUpdating Feedback Request fields...")
            # Update document fields using db_set
            feedback_request.db_set('status', 'Completed', update_modified=True)
            feedback_request.db_set('generated_feedback', json.dumps(feedback, indent=2), update_modified=True)
            feedback_request.db_set('feedback_summary', feedback['overall_feedback'], update_modified=True)
            feedback_request.db_set('completed_at', datetime.now(), update_modified=True)
            feedback_request.db_set('model_used', model_used, update_modified=True)
            feedback_request.db_set('template_used', template_used, update_modified=True)
            
            # Commit changes
            frappe.db.commit()
            
            # Verify the update
            updated_doc = frappe.get_doc("Feedback Request", request_id)
            # Prepare and send message to TAP LMS
            message = {
                "submission_id": feedback_request.submission_id,
                "student_id": feedback_request.student_id,
                "assignment_id": feedback_request.assignment_id,
                "feedback": feedback,
                "is_plagiarized": feedback['plagiarism_output']['is_plagiarized'],
                "is_ai_generated": feedback['plagiarism_output']['is_ai_generated'],
                "match_type": feedback['plagiarism_output']['match_type'],
                "plagiarism_source": feedback['plagiarism_output']['plagiarism_source'],
                "similarity_score": feedback['plagiarism_output']['similarity_score'],
                "ai_detection_source": feedback['plagiarism_output']['ai_detection_source'],
                "ai_confidence": feedback['plagiarism_output']['ai_confidence'],

                "generated_at": feedback_request.completed_at.isoformat() if feedback_request.completed_at else datetime.now().isoformat(),

            }
            
            # Send to TAP LMS queue
            self.queue_manager.send_feedback_to_tap(message)
            
            print(f"\nFeedback processed and sent for request: {request_id}")

            print("Payload sent to TAP LMS queue:")
            print(json.dumps(message, indent=2))
            
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
