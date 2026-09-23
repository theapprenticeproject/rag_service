# rag_service/rag_service/feedback_utils/image_evaluation_both.py

import traceback
from typing import Dict, Tuple

import frappe

from .evaluation_generation import EvaluationGenerator
from ..utils.gcp_service_client import GCPServiceClient


class ImageEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for image submissions."""

    def build_eval_request(
        self, assignment_context: Dict, submission_data: Dict
    ) -> Dict:
        """Everything before the model call, for image submissions. Adds the image
        URL so the nightly Kaapi submit phase can pack it into the batch (Kaapi reads
        the `gs://` URL directly via the registered credential — no signing needed)."""
        req = self._prepare_eval(assignment_context, submission_data, "image")
        req["image_url"] = submission_data.get("submission_url")
        return req

    async def generate_feedback(
        self, assignment_context: Dict, submission_data: Dict, submission_id: str
    ) -> Tuple[Dict, str, str]:
        media_service = None
        media_asset = None
        try:
            print("\n=== Starting AI Feedback Generation (Image) ===")

            req = self.build_eval_request(assignment_context, submission_data)
            llm_provider = req["llm_provider"]
            combined_prompt = req["combined_prompt"]

            if req["provider_name"] == "Kaapi":
                # Kaapi fetches the image itself — hand it a short-lived signed URL so
                # it works for private buckets too (no bytes downloaded locally).
                media_service = GCPServiceClient()
                image_url = media_service.signed_url(req["image_url"])
                response = await llm_provider.generate_with_vision(
                    image_url,
                    combined_prompt,
                    mime_type=None,
                )
            else:
                media_service = GCPServiceClient()
                media_asset = media_service.download_media(req["image_url"])
                response = await llm_provider.generate_with_vision(
                    media_asset,
                    combined_prompt,
                    mime_type=media_asset["mime_type"],
                )
            raw_text = response.text
            self.cost = llm_provider.calculate_cost(response.to_dict())

            self.log_prob_feedback = response.to_dict().get("candidates", [{}])[0].get("avg_logprobs", None)

            print(f"\nRaw LLM Output:\n{raw_text}")
            feedback = self.finalize_feedback(
                raw_text, req["expected_format"], self.cost, self.log_prob_feedback
            )

            print("\n=== Image Feedback Generation Completed ===")
            return feedback, req["model_used"], req["template_used"]

        except Exception as e:
            error_detail = str(e) or repr(e)
            error_msg = (
                f"Error generating image feedback for submission {submission_id}: "
                f"{error_detail}\n{traceback.format_exc()}"
            )
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Image Feedback Generation Error")

            template_used = "Built-in Universal Template for Error"
            error_feedback = self.feedback_service.create_error_feedback(error_detail)
            error_feedback = self._attach_plagiarism_defaults(error_feedback)
            error_feedback['strengths'] = ["cost:1", f"Feedback_LP:0.89", f"Eval_LP:0.78"]
            return error_feedback, "N/A", template_used
        finally:
            if media_service:
                media_service.cleanup(media_asset)
