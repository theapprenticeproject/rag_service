# rag_service/rag_service/feedback_utils/image_evaluation.py

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
            submission_data = {
                "submission_type": "image",
                "submission_url": submission_url,
                "submission_text": None,
            }
            activity_type = assignment_context["assignment"].get("activity_type")
            course_vertical = assignment_context["assignment"].get("course_vertical")

            llm_provider_name = "Gemini"
            llm_provider, model_used = self._create_llm_provider(llm_provider_name)

            template = self.get_prompt_template("image", "both", activity_type, course_vertical)
            expected_format = self._get_expected_format(template)
            assignment_context["assignment"]["rubrics"] = {
  "Content Knowledge": [
    {
      "grade_value": 1,
      "grade_description": "Invalid or random submission — task is blank, off-topic, or unrelated. No link to concept seen."
    },
    {
      "grade_value": 2,
      "grade_description": "Some link to topic visible but full of mistakes or confusion. The student likely didn't understand all steps."
    },
    {
      "grade_value": 3,
      "grade_description": "Main idea is correct and partly applied. Student is trying to use the concept but not yet fully correct."
    },
    {
      "grade_value": 4,
      "grade_description": "Work is accurate, clear, and independently done. Student can apply the concept correctly as shown."
    },
    {
      "grade_value": 5,
      "grade_description": "Work extends the concept meaningfully — connects it to real-life or shows creative application."
    }
  ],
  "Creativity": [
    {
      "grade_value": 1,
      "grade_description": "Invalid or no submission. Task blank or repeated exactly as taught, showing no original input."
    },
    {
      "grade_value": 2,
      "grade_description": "Minor variation without reason. Adds one small change (color, word, step, example) but without any creative link."
    },
    {
      "grade_value": 3,
      "grade_description": "Begins to combine ideas. Connects two or more taught concepts or introduces a small improvement that shows personal thought."
    },
    {
      "grade_value": 4,
      "grade_description": "Applies imagination with purpose. Adjusts or redesigns task elements to make it clearer, more effective, or more interesting."
    },
    {
      "grade_value": 5,
      "grade_description": "Generates and improves ideas. Produces original and relevant solutions by combining, evaluating, or refining ideas; connects learning to real-world or cross-topic contexts."
    }
  ]
}
            print(assignment_context)
            system_prompt, formatted_user_prompt = self._format_prompts(
                template,
                assignment_context,
                submission_data,
                [],
            )
            combined_prompt = f"{system_prompt}\n\n{formatted_user_prompt}"
            print(f"\nCombined Prompt Sent to LLM:\n{combined_prompt}")

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
