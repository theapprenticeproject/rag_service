# rag_service/rag_service/feedback_utils/text_evaluation.py

from typing import Dict, Tuple

import frappe
import traceback

from .evaluation_generation import EvaluationGenerator


class TextEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for text and emoji submissions."""

    async def generate_feedback(
        self, assignment_context: Dict, submission_data: Dict, submission_id: str
    ) -> Tuple[Dict, str, str]:
        try:
            print("\n=== Starting AI Feedback Generation (Text) ===")

            llm_provider, model_used = self._create_llm_provider("Gemini")
            activity_type = assignment_context["assignment"].get("activity_type")
            course_vertical = assignment_context["assignment"].get("course_vertical")

            template = self.get_prompt_template("text", "both", activity_type, course_vertical)
            expected_format = self._get_expected_format(template)
            system_prompt, formatted_user_prompt = self._format_prompts(
                template,
                assignment_context,
                submission_data,
                [],
            )
            

            response, cost, _ = await llm_provider.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": formatted_user_prompt},
                ]
            )

            feedback = self._parse_feedback(response, expected_format)
            feedback = self._attach_plagiarism_defaults(feedback)
            feedback = self._attach_default_fileds(feedback)
            feedback["strengths"] = [f"cost:{cost}", "Feedback_LP:0.0", "Eval_LP:0.0"]
            print("$$$$$$$$$$$$$$")
            print(feedback)

            return feedback, model_used, self._template_used_name(template)

        except Exception as e:
            error_msg = f"Error generating text feedback for submission {submission_id}: {str(e)}\n{traceback.format_exc()}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Text Feedback Generation Error")
            error_feedback = self.feedback_service.create_error_feedback(str(e))
            error_feedback = self._attach_plagiarism_defaults(error_feedback)
            error_feedback["strengths"] = ["cost:0", "Feedback_LP:0.0", "Eval_LP:0.0"]
            return error_feedback, "N/A", "Built-in Universal Template for Error"
