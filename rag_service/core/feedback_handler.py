# rag_service/rag_service/core/feedback_handler.py

import frappe
import json
from datetime import datetime
from typing import Dict, Optional
from ..core.feedback_service import FeedbackService
from ..core.assignment_context_manager import AssignmentContextManager
from ..utils.queue_manager import QueueManager

class FeedbackHandler:
    def __init__(self):
        self.feedback_service = FeedbackService()
        self.queue_manager = QueueManager()
        self.assignment_context_manager = AssignmentContextManager()

    async def handle_submission(self, message_data: Dict) -> None:
        """Handle a new submission from plagiarism queue"""
        request_id = None
        try:

            # Create or update feedback request
            request_id = await self.create_feedback_request(message_data)
            print(f"\nFeedback Request Created/Updated: {request_id}")
            
            # Get assignment context
            assignment_context = await self.assignment_context_manager.get_assignment_context(
                message_data["assignment_id"], message_data["student_id"]
            )
            
            if not assignment_context:
                raise ValueError(f"Could not get context for assignment: {message_data['assignment_id']}")
            
            print("\nGenerating feedback...")
            # Generate feedback
            feedback, model_used, template_used = await self.feedback_service.generate_feedback(
                assignment_context=assignment_context,
                submission_url=message_data["img_url"],
                submission_id=request_id,
                plagiarism_data=message_data,
                feedback_request_id=request_id
            )
            
            print("\nFeedback generated, processing feedback...")
            # Process and deliver feedback
            await self.feedback_service.process_feedback(request_id, feedback, model_used, template_used)
            print("\nFeedback processing completed")
            
        except Exception as e:
            error_msg = f"Error handling submission: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Submission Handler Error")
            
            # Mark request as failed if it exists
            if request_id and frappe.db.exists("Feedback Request", request_id):
                await self.mark_request_failed(request_id, str(e))
            raise

    async def create_feedback_request(self, message_data: Dict) -> str:
        """Create or update feedback request"""
        try:
            print("\n=== Creating/Updating Feedback Request ===")
            
            # Check for existing request
            existing_requests = frappe.get_list(
                "Feedback Request",
                filters={
                    "submission_id": message_data["submission_id"]
                }
            )
            
            if existing_requests:
                request_id = existing_requests[0].name
                print(f"\nUpdating existing feedback request: {request_id}")
                
                # Get and update existing document
                feedback_request = frappe.get_doc("Feedback Request", request_id)
                feedback_request.processing_attempts += 1
                feedback_request.status = "Processing"
                feedback_request.error_log = None  # Clear previous errors
                feedback_request.save()
                
            else:
                # Create new document using frappe.new_doc()
                feedback_request = frappe.new_doc("Feedback Request")
                feedback_request.update({
                    "submission_id": message_data["submission_id"],
                    "student_id": message_data["student_id"],
                    "assignment_id": message_data["assignment_id"],
                    "submission_content": message_data["img_url"],
                    "plagiarism_score": message_data.get("plagiarism_score", 0.0),
                    "is_plagiarized": message_data.get("is_plagiarized", False),
                    "plagiarism_source": message_data.get("plagiarism_source", "none"),
                    "match_type": message_data.get("match_type", "original"),
                    "is_ai_generated": message_data.get("is_ai_generated", False),
                    "ai_confidence": message_data.get("ai_confidence", 0.0),
                    "similar_sources": json.dumps(message_data.get("similar_sources", [])),
                    "ai_detection_source": message_data.get("ai_detection_source", "unknown"),
                    "status": "Processing",
                    "created_at": datetime.now(),
                    "processing_attempts": 1
                })
                feedback_request.insert()
                request_id = feedback_request.name
                print(f"\nCreated new feedback request: {request_id}")
            
            # Explicitly commit the transaction
            frappe.db.commit()
            return request_id
            
        except Exception as e:
            error_msg = f"Error creating feedback request: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.db.rollback()  # Rollback on error
            frappe.log_error(error_msg, "Feedback Request Creation Error")
            raise

    async def mark_request_failed(self, request_id: str, error_message: str) -> None:
        """Mark feedback request as failed"""
        try:
            print(f"\nMarking request as failed: {request_id}")
            
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            
            # Update status and error log
            feedback_request.status = "Failed"
            feedback_request.error_log = error_message
            feedback_request.completed_at = datetime.now()
            feedback_request.save()
            
            # Commit changes
            frappe.db.commit()
            
            print("Request marked as failed successfully")
            
        except Exception as e:
            error_msg = f"Error marking request as failed: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.db.rollback()
            frappe.log_error(error_msg, "Request Failure Update Error")

    async def get_request_status(self, request_id: str) -> Dict:
        """Get status of a feedback request"""
        try:
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            
            status = {
                "request_id": feedback_request.name,
                "submission_id": feedback_request.submission_id,
                "status": feedback_request.status,
                "created_at": feedback_request.created_at,
                "completed_at": feedback_request.completed_at,
                "processing_attempts": feedback_request.processing_attempts,
                "has_feedback": bool(feedback_request.generated_feedback),
                "has_error": bool(feedback_request.error_log),
                "error_message": feedback_request.error_log if feedback_request.error_log else None
            }
            
            return status
            
        except frappe.DoesNotExistError:
            return {
                "error": "Request not found",
                "request_id": request_id,
                "status": "Not Found"
            }
        except Exception as e:
            error_msg = f"Error getting request status: {str(e)}"
            print(f"\nError: {error_msg}")
            return {
                "error": error_msg,
                "request_id": request_id,
                "status": "Unknown"
            }

    async def cleanup_old_requests(self, days: int = 30) -> None:
        """Clean up old completed requests"""
        try:
            print(f"\n=== Cleaning Up Old Requests (>{days} days) ===")
            
            cutoff_date = datetime.now() - datetime.timedelta(days=days)
            
            old_requests = frappe.get_list(
                "Feedback Request",
                filters={
                    "status": "Completed",
                    "created_at": ["<", cutoff_date]
                }
            )
            
            for request in old_requests:
                frappe.delete_doc("Feedback Request", request.name)
                
            frappe.db.commit()
            
            print(f"Cleaned up {len(old_requests)} old requests")
            
        except Exception as e:
            error_msg = f"Error cleaning up old requests: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.db.rollback()
            frappe.log_error(error_msg, "Cleanup Error")
            raise
