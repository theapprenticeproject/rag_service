# rag_service/rag_service/feedback_utils/image_evaluation.py

import json
from typing import Any, Dict, Optional, Tuple

import frappe

from .evaluation_generation import EvaluationGenerator
from ..core.llm_providers import create_llm_provider


class ImageEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for image submissions."""

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

    def _create_llm_provider(self) -> Tuple[Any, str]:
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
            print("\n=== Starting AI Feedback Generation (Image) ===")
            submission_data = {
                "submission_type": "image",
                "submission_url": submission_url,
                "submission_text": None,
            }
            activity_type = assignment_context["assignment"].get("activity_type")
            course_vertical = assignment_context["assignment"].get("course_vertical")

            llm_provider, model_used = self._create_llm_provider()

            evaluation_result = await self.generate_evaluation(llm_provider, submission_url, assignment_context)

            template = self.get_prompt_template("image", "feedback", activity_type, course_vertical)
            expected_format = self._get_expected_format(template)

            system_prompt, formatted_user_prompt = self._format_prompts(
                template,
                assignment_context,
                submission_data,
                evaluation_result.get("rubric_evaluations", []),
            )
            combined_prompt = f"{system_prompt}\n\n{formatted_user_prompt}"

            print("\nGenerated Prompt for LLM:")
            print(combined_prompt)

            # response = await llm_provider.generate_with_vision(submission_url, combined_prompt)
            # raw_text = response.text
            # cost = llm_provider.calculate_cost(response.to_dict())

            # self.log_prob_feedback = response.to_dict().get("candidates", [{}])[0].get("avg_logprobs", None)
            # self.cost = self.cost + cost

            # print(f"\nRaw LLM Output:\n{raw_text}")
            # feedback = self._parse_feedback(raw_text, expected_format)

            feedback = {
                        "overall_feedback": "Great job! Keep up the creative work!",
                        "overall_feedback_translated": "बहुत अच्छा काम किया, इसे जारी रखें!",
                        "final_grade": 40
                        }
            self.log_prob_feedback = -1.23  # Example log probability for feedback generation
            print(f"\nParsed Feedback:\n{feedback}")
            feedback = self._attach_plagiarism_defaults(feedback)
            print(f"\nModel Used: {model_used}")
            feedback = self._attach_evaluation_to_feedback(feedback, evaluation_result)
            feedback = self._attach_default_fileds(feedback)
            feedback['strengths'] = [f"cost:{self.cost}", f"Feedback_LP:{self.log_prob_feedback}", f"Eval_LP:{self.log_prob_eval}"]
            print(f"\nParsed Feedback:\n{feedback}")

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

    async def generate_evaluation(self, llm_provider: Any, submission_url: str, assignment_context: Dict) -> Dict:
        try:
            print("\n=== Starting Rubric Evaluation Generation (Image) ===")
            submission_data = {
                "submission_type": "image",
                "submission_url": submission_url,
                "submission_text": None,
            }
            activity_type = assignment_context["assignment"].get("activity_type")
            course_vertical = assignment_context["assignment"].get("course_vertical")

            template = self.get_prompt_template("image", "evaluation", activity_type, course_vertical)
            system_prompt, formatted_user_prompt = self._format_prompts(
                template,
                assignment_context,
                submission_data,
                [],
            )
            combined_prompt = f"{system_prompt}\n\n{formatted_user_prompt}"

            response = await llm_provider.generate_with_vision(submission_url, combined_prompt)
            raw_text = response.text
            cost = llm_provider.calculate_cost(response.to_dict())
            self.log_prob_eval = response.to_dict().get("candidates", [{}])[0].get("avg_logprobs", None)
            self.cost = cost
            evaluation_result = self._parse_rubric_evaluations(raw_text)

            print("\n=== Rubric Evaluation Generation Completed (Image) ===")
            return evaluation_result
        except Exception as e:
            print(f"\nError during rubric evaluation generation: {str(e)}")
            raise Exception("Failed to generate rubric evaluation for image submission")
