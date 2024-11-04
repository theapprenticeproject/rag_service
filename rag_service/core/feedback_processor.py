# rag_service/rag_service/core/feedback_processor.py

import frappe
import json
from datetime import datetime
from typing import Dict, Optional
from ..utils.queue_manager import QueueManager

class FeedbackProcessor:
    def __init__(self):
        self.queue_manager = QueueManager()

    def process_feedback(self, feedback_request_id: str, feedback_data: Dict) -> None:
        """Process and store feedback, then queue for delivery"""
        try:
            print(f"\n=== Processing Feedback for Request {feedback_request_id} ===")
            
            # Update feedback request
            self.update_feedback_request(feedback_request_id, feedback_data)
            
            # Prepare feedback for TAP LMS
            tap_feedback = self.prepare_tap_feedback(feedback_request_id, feedback_data)
            
            # Send to TAP LMS queue
            self.queue_manager.send_feedback_to_tap(tap_feedback)
            
            print(f"\nFeedback processed and queued successfully for {feedback_request_id}")
            
        except Exception as e:
            error_msg = f"Error processing feedback: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Feedback Processing Error")
            self.mark_request_failed(feedback_request_id, str(e))
            raise

    def update_feedback_request(self, request_id: str, feedback: Dict) -> None:
        """Update feedback request with generated feedback"""
        try:
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            
            feedback_request.update({
                "status": "Completed",
                "generated_feedback": json.dumps(feedback),
                "feedback_summary": feedback["overall_feedback"],
                "grade_recommendation": feedback["grade_recommendation"],
                "completed_at": datetime.now(),
                "error_log": None  # Clear any previous errors
            })
            
            feedback_request.save()
            frappe.db.commit()
            
            print(f"\nUpdated Feedback Request: {request_id}")
            
        except Exception as e:
            error_msg = f"Error updating feedback request: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Feedback Update Error")
            raise

    def prepare_tap_feedback(self, request_id: str, feedback: Dict) -> Dict:
        """Prepare feedback data for TAP LMS"""
        try:
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            
            return {
                "submission_id": feedback_request.submission_id,
                "student_id": feedback_request.student_id,
                "assignment_id": feedback_request.assignment_id,
                "feedback": {
                    "overall_feedback": feedback["overall_feedback"],
                    "strengths": feedback["strengths"],
                    "areas_for_improvement": feedback["areas_for_improvement"],
                    "learning_objectives_feedback": feedback["learning_objectives_feedback"],
                    "grade_recommendation": feedback["grade_recommendation"],
                    "encouragement": feedback["encouragement"]
                },
                "generated_at": str(feedback_request.completed_at),
                "grade": self.extract_grade(feedback["grade_recommendation"])
            }
            
        except Exception as e:
            error_msg = f"Error preparing TAP feedback: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Feedback Preparation Error")
            raise

    def extract_grade(self, grade_recommendation: str) -> int:
        """Extract numerical grade from grade recommendation"""
        try:
            # Example format: "85 out of 100, for strong technical execution..."
            grade_str = grade_recommendation.split()[0]
            return int(grade_str)
        except (IndexError, ValueError):
            return 0  # Default grade if parsing fails

    def mark_request_failed(self, request_id: str, error_message: str) -> None:
        """Mark feedback request as failed"""
        try:
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            
            feedback_request.update({
                "status": "Failed",
                "error_log": error_message,
                "completed_at": datetime.now()
            })
            
            feedback_request.save()
            frappe.db.commit()
            
            print(f"\nMarked Feedback Request as failed: {request_id}")
            
        except Exception as e:
            error_msg = f"Error marking request as failed: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Feedback Status Update Error")

    def retry_failed_requests(self) -> None:
        """Retry processing failed feedback requests"""
        try:
            failed_requests = frappe.get_list(
                "Feedback Request",
                filters={
                    "status": "Failed",
                    "processing_attempts": ["<", 3]  # Max 3 attempts
                },
                fields=["name", "processing_attempts"]
            )
            
            for request in failed_requests:
                try:
                    feedback_request = frappe.get_doc("Feedback Request", request.name)
                    feedback_request.processing_attempts += 1
                    feedback_request.status = "Pending"
                    feedback_request.save()
                    
                    print(f"\nRetrying Feedback Request: {request.name}")
                    # The request will be picked up by the normal processing flow
                    
                except Exception as e:
                    print(f"\nError retrying request {request.name}: {str(e)}")
                    continue
                    
        except Exception as e:
            error_msg = f"Error in retry process: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Feedback Retry Error")
