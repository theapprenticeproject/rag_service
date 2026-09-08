# rag_service/rag_service/feedback_utils/video_evaluation.py

import traceback
from typing import Dict, Tuple

import frappe

from .evaluation_generation import EvaluationGenerator
from ..utils.gcp_service_client import GCPServiceClient


class VideoEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for video submissions."""

    async def generate_feedback(
        self, assignment_context: Dict, submission_data: Dict, submission_id: str
    ) -> Tuple[Dict, str, str]:
        media_service = None
        media_asset = None
        try:
            print("\n=== Starting AI Feedback Generation (Video) ===")

            llm_provider, model_used = self._create_llm_provider("Gemini")
            
            activity_type = assignment_context["assignment"].get("activity_type")
            course_vertical = assignment_context["assignment"].get("course_vertical")
            print(f"Activity Type: {activity_type}, Course Vertical: {course_vertical}")


            template = self.get_prompt_template("video", "both", activity_type, course_vertical)
            expected_format = self._get_expected_format(template)

            system_prompt, formatted_user_prompt = self._format_prompts(
                template,
                assignment_context,
                submission_data,
                [],
            )
            combined_prompt = f"{system_prompt}\n\n{formatted_user_prompt}"

            media_service = GCPServiceClient()
            media_asset = media_service.download_media(submission_data["submission_url"])

            response = await llm_provider.generate_with_video(
                media_asset,
                combined_prompt,
                mime_type=media_asset["mime_type"],
            )
            raw_text = response.text
            self.cost = llm_provider.calculate_cost(response.to_dict())

            self.log_prob_feedback = response.to_dict().get("candidates", [{}])[0].get("avg_logprobs", None)

            print(f"\nRaw LLM Output:\n{raw_text}")
            feedback = self._parse_feedback(raw_text, expected_format)
            feedback = self._attach_plagiarism_defaults(feedback)
            feedback = self._attach_default_fileds(feedback)
            feedback['strengths'] = [f"cost:{self.cost}", f"Feedback_LP:{self.log_prob_feedback}", f"Eval_LP:{0.0}"]

            template_used = self._template_used_name(template)

            print("\n===Feedback Generation Completed ===")
            return feedback, model_used, template_used

        except Exception as e:
            error_detail = str(e) or repr(e)
            error_msg = (
                f"Error generating video feedback for submission {submission_id}: "
                f"{error_detail}\n{traceback.format_exc()}"
            )
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Video Feedback Generation Error")

            template_used = "Built-in Universal Template for Error"
            error_feedback = self.feedback_service.create_error_feedback(error_detail)
            error_feedback = self._attach_plagiarism_defaults(error_feedback)
            error_feedback['strengths'] = ["cost:1", f"Feedback_LP:0.89", f"Eval_LP:0.78"]
            return error_feedback, "N/A", template_used
        finally:
            if media_service:
                media_service.cleanup(media_asset)
