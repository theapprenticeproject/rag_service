# rag_service/rag_service/feedback_utils/video_evaluation.py

import json
from typing import Any, Dict, Optional, Tuple

import frappe

from .evaluation_generation import EvaluationGenerator
from ..core.llm_providers import create_llm_provider


class VideoEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for video submissions."""

    def _resolve_service_account_credentials(self, settings: Any) -> Optional[Dict]:
        raw_key = settings.get("credentials_json")

        if isinstance(raw_key, dict):
            return raw_key
        if isinstance(raw_key, str):
            raw_key = raw_key.strip()
            if raw_key:
                try:
                    return json.loads(raw_key)
                except json.JSONDecodeError:
                    return None

        return None

    def _create_llm_provider(self) -> tuple[Any, str]:
        gemini_settings = frappe.get_list("Gemini Settings", filters={"is_active": 1}, limit=1)
        if not gemini_settings:
            raise Exception("No active Gemini configuration found")

        settings = frappe.get_doc("Gemini Settings", gemini_settings[0].name)
        model_used = gemini_settings[0].name

        key_data = self._resolve_service_account_credentials(settings)
        if not key_data:
            raise Exception("Gemini service account key JSON is required")

        llm_provider = create_llm_provider(
            provider="Gemini",
            api_key="",
            model_name=settings.model_name,
            temperature=settings.temperature or 0,
            max_tokens=settings.max_tokens or 2000,
            key_data=key_data,
            location=settings.location or "us-central1",
            project_id=settings.project_id or None,
        )

        return llm_provider, model_used

    async def generate_feedback(
        self, assignment_context: Dict, submission_url: str, submission_id: str
    ) -> Tuple[Dict, str, str]:
        try:
            print("\n=== Starting AI Feedback Generation (Video) ===")

            template = self.get_prompt_template("video")
            expected_format = self._get_expected_format(template)

            formatted_user_prompt = self._format_user_prompt(template, assignment_context, "video")
            system_prompt = template.system_prompt
            combined_prompt = f"{system_prompt}\n\n{formatted_user_prompt}"

            llm_provider, model_used = self._create_llm_provider()

            if not hasattr(llm_provider, "generate_with_video"):
                raise Exception("Gemini provider does not support video generation")

            raw_text = await llm_provider.generate_with_video(submission_url, combined_prompt)
            feedback = self._parse_feedback(raw_text, expected_format)
            feedback = self._attach_plagiarism_defaults(feedback)

            template_used = self._template_used_name(template)

            print("\n=== Video Feedback Generation Completed ===")
            return feedback, model_used, template_used

        except Exception as e:
            error_msg = f"Error generating video feedback for submission {submission_id}: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Video Feedback Generation Error")

            template_used = "Built-in Universal Template for Error"
            error_feedback = self.feedback_service.create_error_feedback()
            error_feedback = self._attach_plagiarism_defaults(error_feedback)
            return error_feedback, "N/A", template_used
