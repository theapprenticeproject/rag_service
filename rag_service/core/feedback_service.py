import frappe
import json
from datetime import datetime
from typing import Dict
from ..feedback_utils.evaluation_generation import EvaluationGenerator
from ..utils.queue_manager import QueueManager
from ..tts.lib.settings import load as load_tts_settings
from ..tts.lib import languages as tts_languages
from ..tts.lib.text import prepare as prepare_tts_text, fingerprint as tts_fingerprint


class FeedbackService:
    def __init__(self):
        self.evaluation_generator = EvaluationGenerator(self)
        self.queue_manager = QueueManager()
        self.model_used = None

    async def generate_feedback( self, assignment_context: Dict, submission_data: Dict, submission_id: str,
                                    plagiarism_data: Dict = None, feedback_request_id: str = None) -> Dict:
        result_status = "Pending"
        model_used = "N/A"
        tempalate_used = "N/A"

        try:
            if plagiarism_data:
                is_plagiarized = plagiarism_data.get("is_plagiarized", False)
                is_ai_generated = plagiarism_data.get("is_ai_generated", False)
                match_type = plagiarism_data.get("match_type", "original")

                if is_ai_generated:
                    result_status = "Success - Flagged"
                    feedback = self._create_ai_generated_feedback(
                        plagiarism_data
                    )
                    tempalate_used = "Feedback Template for AI Generated Submission"

                elif is_plagiarized:
                    result_status = "Success - Flagged"
                    feedback = self._create_plagiarism_feedback(
                        plagiarism_data
                    )
                    tempalate_used = "Feedback Template for Plagiarized Submission"

                else:
                    result_status = "Success - Original"
                    feedback, model_used, tempalate_used = await self.evaluation_generator.generate_ai_feedback(
                        assignment_context, submission_data, submission_id
                    )

            feedback["translation_language"] = assignment_context["student"].get("language", "English")
            await self._update_result_status(feedback_request_id, result_status)
            return feedback, model_used, tempalate_used

        except Exception as e:
            result_status = "Failed"
            await self._update_result_status(feedback_request_id, result_status, str(e))
            raise

    async def process_feedback(
        self, request_id: str, feedback: Dict, model_used: str, template_used: str
    ) -> None:
        try:
            feedback_request = frappe.get_doc("Feedback Request", request_id)
            feedback_request.db_set("status", "Completed", update_modified=True)
            feedback_request.db_set(
                "generated_feedback",
                json.dumps(feedback, indent=2, ensure_ascii=False),
                update_modified=True,
            )
            feedback_request.db_set(
                "feedback_summary", feedback["overall_feedback"], update_modified=True
            )
            feedback_request.db_set("completed_at", datetime.now(), update_modified=True)
            feedback_request.db_set("model_used", model_used, update_modified=True)
            feedback_request.db_set("template_used", template_used, update_modified=True)

            self._decide_audio_eligibility(feedback_request, feedback)

            frappe.db.commit()

            await self.release_to_lms_if_ready(request_id)

        except Exception as e:
            error_msg = f"Error processing feedback: {str(e)}"
            print(f"\nError: {error_msg}")

            try:
                if "feedback_request" in locals() and feedback_request:
                    feedback_request.db_set("status", "Failed", update_modified=True)
                    feedback_request.db_set("error_log", error_msg, update_modified=True)
                    feedback_request.db_set("audio_status", "Not Applicable", update_modified=True)
                    frappe.db.commit()
                    print(f"Request {request_id} marked as failed")
                    await self.release_to_lms_if_ready(request_id)
            except Exception as save_error:
                print(f"Error saving failure status: {str(save_error)}")

            frappe.log_error(error_msg, "Feedback Processing Error")
            raise

    def _decide_audio_eligibility(self, feedback_request, feedback: Dict) -> None:
        try:
            plagiarism = feedback.get("plagiarism_output") or {}
            if plagiarism.get("is_plagiarized") or plagiarism.get("is_ai_generated"):
                feedback_request.db_set("audio_status", "Not Applicable", update_modified=True)
                return

            tts_settings = load_tts_settings()
            if not tts_settings.kill_switch_on():
                feedback_request.db_set("audio_status", "Not Applicable", update_modified=True)
                return

            translated_text = (feedback.get("overall_feedback_translated") or "").strip()
            language = tts_languages.normalize(feedback.get("translation_language") or "English")

            if language is None or not translated_text:
                feedback_request.db_set("audio_status", "Not Applicable", update_modified=True)
                return

            prepared, _truncated = prepare_tts_text(
                translated_text, tts_settings.max_text_chars(), tts_settings.over_length_policy
            )
            if not prepared:
                feedback_request.db_set("audio_status", "Not Applicable", update_modified=True)
                return

            feedback_request.db_set({
                "audio_status": "Queued",
                "audio_source_text": prepared,
                "audio_text_fingerprint": tts_fingerprint(prepared, language),
                "audio_queued_at": datetime.now(),
            }, update_modified=True)

        except Exception as e:
            print(f"\nAudio eligibility check failed, defaulting to Not Applicable: {e}")
            feedback_request.db_set("audio_status", "Not Applicable", update_modified=True)

    async def release_to_lms_if_ready(self, request_id: str) -> None:
        if not request_id or not frappe.db.exists("Feedback Request", request_id):
            return

        row = frappe.get_doc("Feedback Request", request_id)

        if row.sent_to_lms_at:
            return

        if row.audio_status not in ("Not Applicable", "Completed", "Failed", "", None):
            return

        try:
            feedback = json.loads(row.generated_feedback or "{}")
        except Exception:
            feedback = {}

        feedback["audio_feedback_url"] = row.audio_feedback_url or ""

        message = {
            "submission_id": row.submission_id,
            "student_id": row.student_id,
            "assignment_id": row.assignment_id,
            "feedback": feedback,
            "generated_at": (row.completed_at or datetime.now()).isoformat(),
        }

        self.queue_manager.send_feedback_to_tap(message)
        frappe.db.set_value("Feedback Request", request_id, "sent_to_lms_at", frappe.utils.now_datetime())
        frappe.db.commit()

        print("Payload sent to TAP LMS queue:")
        print(json.dumps(message, indent=2, ensure_ascii=False))

    async def _update_result_status(self, feedback_request_id: str, status: str, error_message: str = None):
        if not feedback_request_id:
            return

        update_data = {"result_status": status}
        if error_message:
            update_data["error_message"] = error_message[:500]

        frappe.db.set_value(
            "Feedback Request",
            feedback_request_id,
            update_data,
            update_modified=True
        )
        frappe.db.commit()

    def _create_ai_generated_feedback(self, plagiarism_data: Dict) -> Dict:
        ai_source = plagiarism_data.get("ai_detection_source", "unknown")
        ai_confidence = plagiarism_data.get("ai_confidence", 0.0)
        response = {
            "overall_feedback": "Hi Champ, I found you have sent AI created work. Please send your work. I'm excited to see what you made.",
            "overall_feedback_translated": "Your submission appears to be generated by an AI tool. \
            At MentorMe, we encourage original creative work that reflects your own learning \
            and artistic development. AI-generated images, while interesting, don't demonstrate \
            the skills and creativity we're looking to nurture. Please submit your own original \
            artwork for this assignment.",
            "strengths": ["N/A - AI-generated content detected."],
            "areas_for_improvement": ["Submit original artwork created by you",
                                      "Review assignment guidelines for creative direction"],
            "learning_objectives_feedback": ["N/A - AI-generated content detected."],
            "final_grade": 0,
            "submission_validity": "Invalid" ,
            "encouragement": "We believe in your creative abilities!",
            "rubric_evaluations": [{
                                        "Skill": "Content Knowledge",
                                        "grade_value": 0,
                                        "observation": "N/A - AI-generated content detected."
                                    }],
            "plagiarism_output": {
                "stock_audio_file": "invalid_submission_ai",
                "is_plagiarized": False,
                "is_ai_generated": True,
                "match_type": "ai_generated",
                "plagiarism_source": "none",
                "similarity_score": 0.0,
                "ai_detection_source": ai_source,
                "ai_confidence": ai_confidence,
            }
        }

        return response

    def _create_plagiarism_feedback( self, plagiarism_data: Dict) -> Dict:
        match_type = plagiarism_data.get("match_type")
        plagiarism_source = plagiarism_data.get("plagiarism_source")
        similarity_score = plagiarism_data.get("similarity_score", 0.0)
        ai_confidence = plagiarism_data.get("ai_confidence", 0.0)

        if plagiarism_source =="peer":
            feedback = "Hi Champ, I found you have sent another student's work. Please send your work. No cheating this time. Excited to see what you made."
        elif plagiarism_source =="self":
            feedback = "Hi Champ, I found you have sent the same work again. Please resend the final work. Excited to see what you made."
        elif plagiarism_source == "reference":
            feedback = "Hi Champ, I found you have sent work that closely matches reference materials. Please submit your own original work. I'm excited to see your unique creation!"
        else:
            feedback = "Hi Champ, I found you have sent work that closely matches existing content. Please submit your own original work. I'm excited to see your unique creation!"
        response = {
            "overall_feedback": feedback,
            "overall_feedback_translated": feedback,
            "strengths": ["N/A - Submission flagged for similarity"],
            "areas_for_improvement": ["Create original artwork for this assignment",
                                      "Review academic integrity guidelines"],
            "learning_objectives_feedback": ["N/A - Submission flagged for similarity"],
            "final_grade": 0,
            "submission_validity": "Invalid" ,
            "encouragement": "Every artist develops their unique style through practice!",
            "rubric_evaluations": [{
                                        "Skill": "Content Knowledge",
                                        "grade_value": 0,
                                        "observation": "N/A - Submission flagged for similarity."
                                    },
                                    {
                                        "Skill": "Creativity",
                                        "grade_value": 0,
                                        "observation": "N/A - Submission flagged for similarity."
                                    }],
            "plagiarism_output": {
                "stock_audio_file": "invalid_submission_ai",
                "is_plagiarized": True,
                "is_ai_generated": False,
                "match_type": match_type,
                "plagiarism_source": plagiarism_source,
                "similarity_score": similarity_score,
                "ai_detection_source": "none",
                "ai_confidence": ai_confidence,
            }
        }

        return response 

    def validate_feedback_structure(self, feedback: Dict, expected_format: Dict) -> Dict:
        for field in expected_format:
            if field not in feedback:
                if isinstance(expected_format[field], list):
                    feedback[field] = ["No information provided"]
                elif isinstance(expected_format[field], (int, float)):
                    feedback[field] = 0
                else:
                    feedback[field] = "No information provided"

        try:
            grade = feedback.get("final_grade", 0)
            if isinstance(grade, str):
                grade_clean = ''.join(c for c in grade if c.isdigit() or c == '.')
                grade = float(grade_clean) if grade_clean else 0
            feedback["final_grade"] = max(0, min(100, float(grade)))
        except (ValueError, TypeError):
            feedback["final_grade"] = 0

        list_fields = ["strengths", "areas_for_improvement", "learning_objectives_feedback"]
        for field in list_fields:
            if field in feedback and not isinstance(feedback[field], list):
                feedback[field] = [str(feedback[field])]

        return feedback

    # def create_fallback_feedback(self, expected_format: Dict) -> Dict:
    #     """Create structured fallback when JSON parsing fails"""
        
    #     fallback = {}
    #     for field, default_value in expected_format.items():
    #         if field == "overall_feedback":
    #             fallback[field] = "I encountered a system error while processing your submission. This appears to be a technical issue on our end. Please try resubmitting, and if the issue persists, contact your instructor."
    #         elif field == "overall_feedback_translated":
    #             fallback[field] = "I encountered a system error while processing your submission. This appears to be a technical issue on our end. Please try resubmitting, and if the issue persists, contact your instructor."
    #         elif field == "final_grade":
    #             fallback[field] = 50  # Neutral grade for technical issues
    #         elif field == "rubric_evaluations":
    #             fallback[field] = [
    #             {
    #             "skill": "Content Knowledge",
    #             "grade_value": 2,
    #             "observation": "Neutral evaluation due to processing issue"
    #             }
    #         ]
    #         elif isinstance(default_value, list):
    #             if "strength" in field:
    #                 fallback[field] = ["Your submission was received and processed"]
    #             elif "improvement" in field:
    #                 fallback[field] = ["Please ensure your submission clearly shows your work"]
    #             else:
    #                 fallback[field] = ["Unable to provide specific feedback due to processing issue"]
    #         else:
    #             if field == "encouragement":
    #                 fallback[field] = "Technical issues don't reflect your effort - please try resubmitting!"
    #             else:
    #                 fallback[field] = "Processing issue - please resubmit for detailed feedback"
        
    #     return fallback

    def create_error_feedback(self, error_msg: str) -> Dict:
        feedback = {
            "overall_feedback": "I encountered a system error while processing your submission. This appears to be a technical issue on our end.",
            "overall_feedback_translated": "I encountered a system error while processing your submission. This appears to be a technical issue on our end. ",
            "strengths": [f"Your submission was received successfully but system encountered an error during processing.{error_msg}"],
            "areas_for_improvement": ["No issues identified with your submission - this appears to be a technical problem"],
            "learning_objectives_feedback": ["Unable to evaluate due to system error."],
            "final_grade": 0,
            "submission_validity": "Valid" ,
            "encouragement": "Technical issues don't reflect your effort or ability.",
            "rubric_evaluations": [{
                                        "Skill": "Content Knowledge",
                                        "grade_value": 2,
                                        "observation": "Neutral evaluation due to processing issue"
                                    }],
        }
        plagiarism_output = {
                "is_plagiarized": False,
                "is_ai_generated": False,
                "match_type": "original",
                "plagiarism_source": "none",
                "similarity_score": 0.0,
                "ai_detection_source": "none",
                "ai_confidence": 0.0,
                "similar_sources": []
            }
        feedback["plagiarism_output"] = plagiarism_output

        return feedback
