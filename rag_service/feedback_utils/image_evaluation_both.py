# rag_service/rag_service/feedback_utils/image_evaluation_both.py

import json
from typing import Any, Dict, Optional, Tuple

import frappe

from .evaluation_generation import EvaluationGenerator
from ..core.llm_providers import create_llm_provider


class ImageEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for image submissions."""

    def _create_llm_provider(self,llm_provider_name) -> Tuple[Any, str]:
        llm_settings = frappe.get_list("LLM Settings", 
                                       filters={"is_active": 1, "provider": llm_provider_name}, limit=1)
        
        if not llm_settings:
            raise Exception(f"No active {llm_provider_name} configuration found")

        settings = frappe.get_doc("LLM Settings", llm_settings[0].name)
        model_used = llm_settings[0].name

        llm_provider = create_llm_provider(
            provider=llm_provider_name,
            api_key="",
            model_name=settings.model_name,
            temperature=settings.temperature or 0,
            max_tokens=settings.max_tokens or 2000,
            settings=settings,
        )

        return llm_provider, model_used
    

    async def generate_feedback(
        self, assignment_context: Dict, submission_url: str, submission_id: str
    ) -> Tuple[Dict, str, str]:
        try:
            print("\n=== Starting AI Feedback Generation (Image) ===")

            llm_provider_name = "Gemini"
            llm_provider, model_used = self._create_llm_provider(llm_provider_name)
            
            activity_type = assignment_context["assignment"].get("activity_type")
            course_vertical = assignment_context["assignment"].get("course_vertical")


            template = self.get_prompt_template("image", "both", activity_type, course_vertical)
            expected_format = self._get_expected_format(template)

            formatted_user_prompt = self._format_user_prompt(
                template,
                assignment_context,
                "image",
                [],
            )
            combined_prompt = f"{template.system_prompt}\n\n{formatted_user_prompt}"

            response = await llm_provider.generate_with_vision(submission_url, combined_prompt)
            raw_text = response.text
            self.cost = llm_provider.calculate_cost(response.to_dict())

            self.log_prob_feedback = response.to_dict().get("candidates", [{}])[0].get("avg_logprobs", None)

            print(f"\nRaw LLM Output:\n{raw_text}")
            feedback = self._parse_feedback(raw_text, expected_format)
            feedback = self._attach_plagiarism_defaults(feedback)
            feedback = self._attach_default_fileds(feedback)
            feedback['strengths'] = [f"cost:{self.cost}", f"Feedback_LP:{self.log_prob_feedback}", f"Eval_LP:{0.0}"]

            template_used = self._template_used_name(template)

            print("\n=== Image Feedback Generation Completed ===")
            return feedback, model_used, template_used

        except Exception as e:
            error_msg = f"Error generating image feedback for submission {submission_id}: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Image Feedback Generation Error")

            template_used = "Built-in Universal Template for Error"
            error_feedback = self.feedback_service.create_error_feedback(str(e))
            error_feedback = self._attach_plagiarism_defaults(error_feedback)
            error_feedback['strengths'] = ["cost:1", f"Feedback_LP:0.89", f"Eval_LP:0.78"]
            return error_feedback, "N/A", template_used

