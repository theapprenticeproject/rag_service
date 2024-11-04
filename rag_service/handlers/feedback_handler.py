# rag_service/rag_service/handlers/feedback_handler.py

import frappe
import json
from datetime import datetime
from typing import Dict, Optional
from ..core.langchain_manager import LangChainManager
from ..core.feedback_processor import FeedbackProcessor
from ..utils.queue_manager import QueueManager

class FeedbackHandler:
    def __init__(self):
        self.langchain_manager = LangChainManager()
        self.feedback_processor = FeedbackProcessor()
        self.queue_manager = QueueManager()

    async def handle_submission(self, message_data: Dict) -> None:
        """Handle a new submission from plagiarism queue"""
        request_id = None
        try:
            print("\n=== Processing New Submission ===")
            print(f"Submission ID: {message_data.get('submission_id')}")
            
            # Create or update feedback request
            request_id = await self.create_feedback_request(message_data)
            
            # Get assignment context
            assignment_context = await self.get_assignment_context(message_data["assignment_id"])
            
            # Generate feedback
            feedback = await self.langchain_manager.generate_feedback(
                assignment_context=assignment_context,
                submission_url=message_data["img_url"],
                submission_id=request_id
            )
            
            # Process and deliver feedback
            await self.process_feedback(request_id, feedback)
            
        except Exception as e:
            error_msg = f"Error handling submission: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Submission Handler Error")
            
            # Mark request as failed if it exists
            if request_id:
                await self.mark_request_failed(request_id, str(e))
            raise

    async def create_feedback_request(self, message_data: Dict) -> str:
        """Create or update feedback request"""
        try:
            # Check for existing request
            existing_requests = frappe.get_list(
                "Feedback Request",
                filters={
                    "submission_id": message_data["submission_id"]
                }
            )
            
            if existing_requests:
                feedback_request = frappe.get_doc(
                    "Feedback Request", 
                    existing_requests[0].name
                )
                feedback_request.processing_attempts += 1
                feedback_request.status = "Processing"
            else:
                feedback_request = frappe.get_doc({
                    "doctype": "Feedback Request",
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
            
            feedback_request.save()
            frappe.db.commit()
            
            print(f"\nFeedback Request Created/Updated: {feedback_request.name}")
            return feedback_request.name
            
        except Exception as e:
            error_msg = f"Error creating feedback request: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Feedback Request Creation Error")
            raise

    async def get_assignment_context(self, assignment_id: str) -> Dict:
        """Get assignment context from cache or create new"""
        try:
            # Check cache
            cached_context = frappe.get_list(
                "Assignment Context",
                filters={
                    "assignment_id": assignment_id,
                    "cache_valid_till": [">", datetime.now()]
                },
                limit=1
            )
            
            if cached_context:
                context = frappe.get_doc("Assignment Context", cached_context[0].name)
                return {
                    "assignment": {
                        "id": context.assignment_id,
                        "name": context.assignment_name,
                        "type": context.course_vertical,
                        "description": context.learning_objectives,
                    },
                    "learning_objectives": json.loads(context.learning_objectives or '[]'),
                    "rubric": json.loads(context.rubric or '[]')
                }
            
            # If not in cache, create new context
            try:
                assignment = frappe.get_doc("Assignment Context", {"assignment_id": assignment_id})
            except frappe.DoesNotExistError:
                # Create new Assignment Context
                assignment = frappe.get_doc({
                    "doctype": "Assignment Context",
                    "assignment_id": assignment_id,
                    "assignment_name": assignment_id,
                    "course_vertical": "visual_arts",
                    "learning_objectives": json.dumps([
                        {"description": "Create an original cartoon character"}
                    ]),
                    "rubric": json.dumps([]),
                    "difficulty_level": "Medium",
                    "last_updated": datetime.now(),
                    "version": 1,
                    "cache_valid_till": datetime.now().replace(hour=23, minute=59, second=59),
                    "last_sync_status": "Success"
                })
                assignment.insert()
                frappe.db.commit()
            
            return {
                "assignment": {
                    "id": assignment.assignment_id,
                    "name": assignment.assignment_name,
                    "type": assignment.course_vertical,
                    "description": assignment.learning_objectives,
                },
                "learning_objectives": json.loads(assignment.learning_objectives),
                "rubric": json.loads(assignment.rubric)
            }
            
        except Exception as e:
            error_msg = f"Error getting assignment context: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Context Fetch Error")
            raise

    async def process_feedback(self, request_id: str, feedback: Dict) -> None:
        """Process and deliver feedback"""
        try:
            # Update feedback request
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            feedback_request.status = "Completed"
            feedback_request.generated_feedback = json.dumps(feedback)
            feedback_request.feedback_summary = self.langchain_manager.format_feedback_for_display(feedback)
            feedback_request.completed_at = datetime.now()
            feedback_request.save()
            
            # Prepare message for TAP LMS
            message = {
                "submission_id": feedback_request.submission_id,
                "student_id": feedback_request.student_id,
                "assignment_id": feedback_request.assignment_id,
                "feedback": feedback,
                "summary": feedback_request.feedback_summary,
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
            frappe.log_error(error_msg, "Feedback Processing Error")
            raise

    async def mark_request_failed(self, request_id: str, error_message: str) -> None:
        """Mark feedback request as failed"""
        try:
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            feedback_request.status = "Failed"
            feedback_request.error_log = error_message
            feedback_request.save()
            frappe.db.commit()
        except Exception as e:
            print(f"\nError marking request as failed: {str(e)}")
