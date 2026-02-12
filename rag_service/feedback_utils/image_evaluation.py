# rag_service/rag_service/feedback_utils/image_evaluation.py

from typing import Any, Dict, Tuple

import frappe

from .evaluation_generation import EvaluationGenerator
from ..core.llm_providers import create_llm_provider


class ImageEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for image submissions."""

    def _create_llm_provider(self) -> Tuple[Any, str]:
        llm_settings = frappe.get_list("LLM Settings", filters={"is_active": 1}, limit=1)
        if not llm_settings:
            raise Exception("No active LLM configuration found")

        settings = frappe.get_doc("LLM Settings", llm_settings[0].name)
        model_used = llm_settings[0].name

        llm_provider = create_llm_provider(
            provider=settings.provider,
            api_key=settings.get_password("api_secret"),
            model_name=settings.model_name,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
        )

        return llm_provider, model_used

    async def generate_feedback(
        self, assignment_context: Dict, submission_url: str, submission_id: str
    ) -> Tuple[Dict, str, str]:
        try:
            print("\n=== Starting AI Feedback Generation (Image) ===")

            template = self.get_prompt_template("image")
            expected_format = self._get_expected_format(template)

            formatted_user_prompt = self._format_user_prompt(template, assignment_context, "image")
            system_prompt = template.system_prompt

            llm_provider, model_used = self._create_llm_provider()

            messages = llm_provider.format_messages(
                system_prompt=system_prompt,
                user_prompt=formatted_user_prompt,
                image_url=submission_url,
            )

            raw_text = await llm_provider.generate_with_vision(messages)
            feedback = self._parse_feedback(raw_text, expected_format)
            feedback = self._attach_plagiarism_defaults(feedback)

            template_used = self._template_used_name(template)

            print("\n=== Image Feedback Generation Completed ===")
            return feedback, model_used, template_used

        except Exception as e:
            error_msg = f"Error generating image feedback for submission {submission_id}: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Image Feedback Generation Error")

            template_used = "Built-in Universal Template for Error"
            error_feedback = self.feedback_service.create_error_feedback()
            error_feedback = self._attach_plagiarism_defaults(error_feedback)
            return error_feedback, "N/A", template_used
