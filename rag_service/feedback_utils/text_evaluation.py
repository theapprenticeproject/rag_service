# rag_service/rag_service/feedback_utils/text_evaluation.py

from typing import Dict, Tuple

import frappe
import traceback

from .evaluation_generation import EvaluationGenerator


class TextEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for text and emoji submissions."""

    def build_eval_request(
        self, assignment_context: Dict, submission_data: Dict
    ) -> Dict:
        """Everything before the model call, for text/emoji submissions (no image)."""
        return self._prepare_eval(assignment_context, submission_data, "text")

    async def generate_feedback(
        self, assignment_context: Dict, submission_data: Dict, submission_id: str
    ) -> Tuple[Dict, str, str]:
        try:
            print("\n=== Starting AI Feedback Generation (Text) ===")

            req = self.build_eval_request(assignment_context, submission_data)

            response, cost, _ = await req["llm_provider"].generate(
                [
                    {"role": "system", "content": req["system_prompt"]},
                    {"role": "user", "content": req["formatted_user_prompt"]},
                ]
            )

            feedback = self.finalize_feedback(response, req["expected_format"], cost, None)
            print("$$$$$$$$$$$$$$")
            print(feedback)

            return feedback, req["model_used"], req["template_used"]

        except Exception as e:
            error_msg = f"Error generating text feedback for submission {submission_id}: {str(e)}\n{traceback.format_exc()}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Text Feedback Generation Error")
            error_feedback = self.feedback_service.create_error_feedback(str(e))
            error_feedback = self._attach_plagiarism_defaults(error_feedback)
            error_feedback["strengths"] = ["cost:0", "Feedback_LP:0.0", "Eval_LP:0.0"]
            return error_feedback, "N/A", "Built-in Universal Template for Error"
