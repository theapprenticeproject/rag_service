# rag_service/rag_service/feedback_utils/audio_evaluation.py

import traceback
from typing import Dict, Tuple

import frappe

from .evaluation_generation import EvaluationGenerator
from ..utils.gcp_service_client import GCPServiceClient


class AudioEvaluationGenerator(EvaluationGenerator):
    """Generate AI feedback for audio submissions.

    Two paths live here, same shape as ImageEvaluationGenerator/VideoEvaluationGenerator:

    1. build_eval_request() — the "before the model call" half. Renders the prompt
       and attaches the audio_url. Called by batch_runner.py during the nightly
       Kaapi submit phase (for_assessment=True); no model call happens here, the
       rendered prompt + audio URL get packed into the Kaapi batch instead.

    2. generate_feedback() — the real-time fallback path. Only used if batch mode /
       Kaapi assessments are disabled (RAG_BATCH_MODE=0 and Kaapi Settings.enable_assessments
       is off). Calls the model directly (Kaapi's /llm/call proxy, or Gemini directly,
       depending on LLM Settings), exactly like image/video's real-time fallback.
    """

    def build_eval_request(
        self, assignment_context: Dict, submission_data: Dict, *, for_assessment: bool = False
    ) -> Dict:
        """Everything before the model call, for audio submissions. Adds the audio
        URL so the nightly Kaapi submit phase can pack it into the batch (Kaapi reads
        the `gs://` URL directly via the registered google-gcp credential — no signing
        needed, same pattern as image/video)."""
        req = self._prepare_eval(
            assignment_context, submission_data, "audio", for_assessment=for_assessment
        )
        req["audio_url"] = submission_data.get("submission_url")
        return req

    async def generate_feedback(
        self, assignment_context: Dict, submission_data: Dict, submission_id: str
    ) -> Tuple[Dict, str, str]:
        """Real-time fallback path (used only when batch mode / Kaapi assessments
        are disabled). Mirrors image_evaluation_both.py's and video_evaluation.py's
        exact branching: Kaapi provider gets a signed URL (it fetches the bytes
        itself), any other provider downloads the media locally first."""
        media_service = None
        media_asset = None
        try:
            print("\n=== Starting AI Feedback Generation (Audio) ===")

            req = self.build_eval_request(assignment_context, submission_data)
            llm_provider = req["llm_provider"]
            combined_prompt = req["combined_prompt"]

            if req["provider_name"] == "Kaapi":
                # Kaapi fetches the audio itself — hand it a short-lived signed URL so
                # it works for private buckets too (no bytes downloaded locally).
                media_service = GCPServiceClient()
                audio_url = media_service.signed_url(req["audio_url"])
                response = await llm_provider.generate_with_audio(
                    audio_url,
                    combined_prompt,
                    mime_type=None,
                )
            else:
                media_service = GCPServiceClient()
                media_asset = media_service.download_media(req["audio_url"])
                response = await llm_provider.generate_with_audio(
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

            print("\n=== Audio Feedback Generation Completed ===")
            return feedback, req["model_used"], req["template_used"]

        except Exception as e:
            error_detail = str(e) or repr(e)
            error_msg = (
                f"Error generating audio feedback for submission {submission_id}: "
                f"{error_detail}\n{traceback.format_exc()}"
            )
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Audio Feedback Generation Error")
            raise
        finally:
            if media_service:
                media_service.cleanup(media_asset)