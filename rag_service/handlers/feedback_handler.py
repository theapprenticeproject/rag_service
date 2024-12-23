# rag_service/rag_service/handlers/feedback_handler.py

import frappe
import json
from datetime import datetime
from typing import Dict, Optional
from ..core.langchain_manager import LangChainManager
from ..core.feedback_processor import FeedbackProcessor
from ..core.assignment_context_manager import AssignmentContextManager
from ..utils.queue_manager import QueueManager

class FeedbackHandler:
    def __init__(self):
        self.langchain_manager = LangChainManager()
        self.feedback_processor = FeedbackProcessor()
        self.queue_manager = QueueManager()
        self.assignment_context_manager = AssignmentContextManager()

    async def handle_submission(self, message_data: Dict) -> None:
        """Handle a new submission from plagiarism queue"""
        request_id = None
        try:
            print("\n=== Processing New Submission ===")
            print(f"Submission ID: {message_data.get('submission_id')}")
            
            # Create or update feedback request
            request_id = await self.create_feedback_request(message_data)
            print(f"\nFeedback Request Created/Updated: {request_id}")
            
            # Get assignment context
            print(f"\nFetching assignment context for: {message_data['assignment_id']}")
            assignment_context = await self.assignment_context_manager.get_assignment_context(
                message_data["assignment_id"]
            )
            
            if not assignment_context:
                raise ValueError(f"Could not get context for assignment: {message_data['assignment_id']}")
            
            print("\nGenerating feedback...")
            # Generate feedback
            feedback = await self.langchain_manager.generate_feedback(
                assignment_context=assignment_context,
                submission_url=message_data["img_url"],
                submission_id=request_id
            )
            
            print("\nFeedback generated, processing feedback...")
            # Process and deliver feedback
            await self.feedback_processor.process_feedback(request_id, feedback)
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
                    "similar_sources": json.dumps(message_data.get("similar_sources", [])),
                    "status": "Processing",
                    "created_at": datetime.now(),
                    "processing_attempts": 1
                })
                feedback_request.insert()
                request_id = feedback_request.name
                print(f"\nCreated new feedback request: {request_id}")
            
            # Explicitly commit the transaction
            frappe.db.commit()
            
            print(f"Feedback Request Created/Updated Successfully: {request_id}")
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

    async def retry_failed_request(self, request_id: str) -> None:
        """Retry a failed feedback request"""
        try:
            print(f"\n=== Retrying Failed Request: {request_id} ===")
            
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            
            if feedback_request.status != "Failed":
                raise ValueError(f"Request {request_id} is not in failed state")
            
            if feedback_request.processing_attempts >= 3:
                raise ValueError(f"Maximum retry attempts reached for request {request_id}")
            
            # Prepare message data for reprocessing
            message_data = {
                "submission_id": feedback_request.submission_id,
                "student_id": feedback_request.student_id,
                "assignment_id": feedback_request.assignment_id,
                "img_url": feedback_request.submission_content,
                "plagiarism_score": feedback_request.plagiarism_score,
                "similar_sources": json.loads(feedback_request.similar_sources or '[]')
            }
            
            # Process the request again
            await self.handle_submission(message_data)
            
            print(f"Request {request_id} retried successfully")
            
        except Exception as e:
            error_msg = f"Error retrying request: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Request Retry Error")
            raise

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
