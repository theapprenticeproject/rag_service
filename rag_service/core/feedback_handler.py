# rag_service/rag_service/core/feedback_handler.py

import os
import frappe
import json
from datetime import datetime
from typing import Dict, Optional
from ..core.feedback_service import FeedbackService
from ..core.assignment_context_manager import AssignmentContextManager
from ..utils.queue_manager import QueueManager
from ..utils.submission_data import build_submission_content, normalize_submission_payload

# Submission types that are DEFERRED to the nightly batch when batch mode is on.
# Audio/video are always processed in real time (not handled by the batch grader).
DEFERRED_SUBMISSION_TYPES = {"image", "text", "emoji"}


def batch_mode_enabled() -> bool:
    """When on, image/text submissions are saved as Pending and graded by the
    nightly batch instead of in real time. Toggle off with env RAG_BATCH_MODE=0."""
    return os.environ.get("RAG_BATCH_MODE", "1").strip().lower() not in ("0", "false", "no", "")


class FeedbackHandler:
    def __init__(self):
        self.feedback_service = FeedbackService()
        self.queue_manager = QueueManager()
        self.assignment_context_manager = AssignmentContextManager()

    async def handle_submission(self, message_data: Dict) -> None:
        """Handle a new submission from plagiarism queue"""
        submission_data = normalize_submission_payload(message_data)
        submission_type = (submission_data.get("submission_type") or "").lower()

        # Batch mode: image/text are DEFERRED — save a Pending record and stop
        # (the nightly batch grades them). Audio/video keep processing in real time.
        if batch_mode_enabled() and submission_type in DEFERRED_SUBMISSION_TYPES:
            request_id = await self.create_feedback_request(
                message_data, submission_data, status="Pending"
            )
            print(f"\nDeferred {submission_type} submission to nightly batch: {request_id}")
            return

        request_id = None
        try:
            # Create or update feedback request
            request_id = await self.create_feedback_request(message_data, submission_data)
            print(f"\nFeedback Request Created/Updated: {request_id}")
            
            # Get assignment context
            assignment_context = await self.assignment_context_manager.get_assignment_context(
                message_data["assignment_id"]
            )
            assignment_context["student"] = {
                            "student_id":message_data["student_id"],    
                            "grade":message_data["grade"],
                            "level":message_data["level"],
                            "language": message_data["language"]
                        }
            
            if not assignment_context:
                raise ValueError(f"Could not get context for assignment: {message_data['assignment_id']}")
            
            print("\nGenerating feedback...")
            # Generate feedback
            feedback, model_used, template_used = await self.feedback_service.generate_feedback(
                assignment_context=assignment_context,
                submission_data=submission_data,
                submission_id=request_id,
                plagiarism_data=message_data,
                feedback_request_id=request_id
            )
            
        except Exception as e:
            error_msg = f"Error handling submission: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Submission Handler Error")
            
            # Mark request as failed if it exists
            if request_id and frappe.db.exists("Feedback Request", request_id):
                await self.mark_request_failed(request_id, str(e))
            template_used = "Built-in Universal Template for Error"
            feedback = self.feedback_service.create_error_feedback(str(e))
            feedback = self._attach_plagiarism_defaults(feedback)
            feedback['strengths'] = ["cost:1", f"Feedback_LP:0.89", f"Eval_LP:0.78"]
            model_used = "N/A"
            raise Exception(error_msg)
        finally:
            print("\nFeedback generated, processing feedback...")
            # Process and deliver feedback
            await self.feedback_service.process_feedback(request_id, feedback, model_used, template_used)
            print("\nFeedback processing completed")

    def _attach_plagiarism_defaults(self, feedback: Dict) -> Dict:
        feedback["plagiarism_output"] = {
            "is_plagiarized": False,
            "is_ai_generated": False,
            "match_type": "original",
            "plagiarism_source": "none",
            "similarity_score": 0.0,
            "ai_detection_source": "none",
            "ai_confidence": 0.0,
            "similar_sources": [],
        }
        return feedback


    async def create_feedback_request(self, message_data: Dict, submission_data: Dict,
                                      status: str = "Processing") -> str:
        """Create or update feedback request. `status` lets the caller defer a
        submission (status="Pending") for the nightly batch instead of processing now."""
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
                feedback_request.status = status
                feedback_request.error_log = None  # Clear previous errors
                feedback_request.submission_type = submission_data["submission_type"]
                feedback_request.submission_url = submission_data["submission_url"]
                feedback_request.submission_text = submission_data["submission_text"]
                feedback_request.submission_content = build_submission_content(submission_data)
                feedback_request.grade = message_data.get("grade")
                feedback_request.level = message_data.get("level")
                feedback_request.language = message_data.get("language")
                feedback_request.save()
                
            else:
                # Create new document using frappe.new_doc()
                feedback_request = frappe.new_doc("Feedback Request")
                feedback_request.update({
                    "submission_id": message_data["submission_id"],
                    "student_id": message_data["student_id"],
                    "assignment_id": message_data["assignment_id"],
                    "grade": message_data.get("grade"),
                    "level": message_data.get("level"),
                    "language": message_data.get("language"),
                    "submission_type": submission_data["submission_type"],
                    "submission_url": submission_data["submission_url"],
                    "submission_text": submission_data["submission_text"],
                    "submission_content": build_submission_content(submission_data),
                    "plagiarism_score": message_data.get("plagiarism_score", 0.0),
                    "is_plagiarized": message_data.get("is_plagiarized", False),
                    "plagiarism_source": message_data.get("plagiarism_source", "none"),
                    "match_type": message_data.get("match_type", "original"),
                    "is_ai_generated": message_data.get("is_ai_generated", False),
                    "ai_confidence": message_data.get("ai_confidence", 0.0),
                    "similar_sources": json.dumps(message_data.get("similar_sources", [])),
                    "ai_detection_source": message_data.get("ai_detection_source", "unknown"),
                    "status": status,
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
