# rag_service/rag_service/feedback_utils/evaluation_generation.py

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import frappe
from ..core.llm_providers import create_llm_provider
from ..utils.submission_data import (
    MEDIA_SUBMISSION_TYPES,
    TEXT_SUBMISSION_TYPES,
    format_submission_text_for_prompt,
)

EXPECTED_SUBMISSION_LABELS = {
    "emoji": ["emoji"],
    "word_text_voice": ["text", "audio"],
    "image": ["image"],
    "summary_text_voice": ["text", "audio"],
    "photo_video_artefact": ["image", "video"],
    "video": ["image", "video"],
}


class EvaluationGenerator:
    """Shared evaluation generation utilities for different media types."""

    IMAGE_EXTENSIONS = {
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
        "bmp",
        "tiff",
        "heic",
    }
    VIDEO_EXTENSIONS = {
        "mp4",
        "mov",
        "webm",
        "mkv",
        "avi",
        "mpeg",
        "mpg",
    }
    AUDIO_EXTENSIONS = {
        "mp3",
        "wav",
        "m4a",
        "aac",
        "ogg",
        "flac",
    }

    def __init__(self, feedback_service: Any):
        self.feedback_service = feedback_service

    def detect_media_type(self, submission_data: Dict) -> str:
        """Detect media type using submission_type first, then URL extension as fallback."""
        submission_type = (submission_data.get("submission_type") or "").lower()
        if submission_type in MEDIA_SUBMISSION_TYPES or submission_type in TEXT_SUBMISSION_TYPES:
            return "text" if submission_type in TEXT_SUBMISSION_TYPES else submission_type

        submission_url = submission_data.get("submission_url") or ""
        if not submission_url:
            return "image"

        url_without_query = submission_url.split("?", 1)[0].lower()
        if "." in url_without_query:
            ext = url_without_query.rsplit(".", 1)[-1]
            if ext in self.IMAGE_EXTENSIONS:
                return "image"
            if ext in self.VIDEO_EXTENSIONS:
                return "video"
            if ext in self.AUDIO_EXTENSIONS:
                return "audio"

        if "video" in url_without_query:
            return "video"
        if "audio" in url_without_query:
            return "audio"
        return "image"

    def clean_json_response(self, response: str) -> str:
        """Clean JSON response from various formats."""
        try:
            # Remove markdown code blocks if present
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                code_blocks = response.split("```")
                if len(code_blocks) >= 3:  # At least one code block exists
                    response = code_blocks[1].strip()
                    # Check if the extracted content looks like JSON
                    if not (response.startswith("{") or response.startswith("[")):
                        # If not, try to find JSON in the original response
                        json_start = response.find("{")
                        if json_start >= 0:
                            response = response[json_start:]

            # Try to extract JSON if response starts with explanation
            if not response.strip().startswith("{"):
                json_start = response.find("{")
                if json_start >= 0:
                    response = response[json_start:]

            # Check if the response ends properly
            if not response.strip().endswith("}"):
                json_end = response.rfind("}")
                if json_end >= 0:
                    response = response[: json_end + 1]

            return response.strip()
        except Exception as e:
            print(f"Error cleaning JSON: {str(e)}")
            return response

    def format_objectives(self, objectives: List[Dict]) -> str:
        """Format learning objectives for prompt."""
        if not objectives:
            return ""

        formatted = []
        for i, obj in enumerate(objectives, 1):
            if isinstance(obj, dict):
                description = obj.get("description", obj.get("objective_id", "Unknown objective"))
            else:
                description = str(obj)
            formatted.append(f"{i}. {description}")

        return "\n".join(formatted)

    def format_rubrics(self, rubrics) -> str:
        """Format rubric criteria for prompt."""
        prompt = ""
        if isinstance(rubrics, str):
            rubrics = json.loads(rubrics)
        # if "Creativity" in rubrics:
        #         del rubrics["Creativity"]
        for criterion, grades_list in rubrics.items():
            prompt += f"\n{criterion}:\n"
            for grade_item in grades_list:
                prompt += (
                    f"  Grade {grade_item['grade_value']}: {grade_item['grade_description']}\n"
                )

        return prompt


    def get_prompt_template(self, media_type: str, prompt_type: str, activity_type: str, course_vertical: str):
        """Get active template for the given media type."""
        try:
            print(f"\n=== Getting Prompt Template for {prompt_type}===")

            print(f"Media Type: {media_type}")
            print(f"Course Vertical: {course_vertical}")
            print(f"prompt_type Type: {prompt_type}")
            print(f"activity_type: {activity_type}")



            template_course_vertical = (
                "Science Lab" if course_vertical == "Science" else course_vertical
            )

            templates = frappe.get_list(
                "Prompt Template",
                filters={"is_active": 1, "media_type": media_type, "prompt_type": prompt_type, 
                         "activity_type": activity_type, "course_vertical": template_course_vertical },
                order_by="version desc",
                limit=1,
            )

            template = frappe.get_doc("Prompt Template", templates[0].name)
            print(f"Using {media_type} {prompt_type} template: {template.template_name}")

            template.db_set("last_used", datetime.now())
            self._touch_prompt_segment(template.system_segment)
            self._touch_prompt_segment(template.grading_segment)
            self._touch_prompt_segment(template.subject_segment)
            self._touch_prompt_segment(template.output_segment)
            frappe.db.commit()
            return template

        except Exception as e:
            error_msg = f"Template Error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error("Template Error", error_msg)
            raise Exception("No active template found")


    def _get_expected_format(self, template: Any, prompt_type: str = "feedback") -> Dict:
        try:
            if hasattr(template, "response_format") and template.response_format:
                return json.loads(template.response_format)
        except Exception as e:
            print(f"Invalid JSON in template response format: {e}")
            raise Exception("Active template has invalid response format")


    def _render_prompt_content(self, content: str, prompt_vars: Dict[str, Any]) -> str:
        rendered = content or ""
        for key, value in prompt_vars.items():
            placeholder = "{" + key + "}"
            if placeholder in rendered:
                rendered = rendered.replace(placeholder, str(value))
        return rendered

    def _filter_submission_rules(
        self,
        assignment_context: Dict,
        submission_data: Dict,
    ) -> Dict[str, Any]:
        submission_rules = assignment_context.get("assignment", {}).get("submission_rules", [])
        if isinstance(submission_rules, str):
            try:
                submission_rules = json.loads(submission_rules)
            except json.JSONDecodeError:
                return {}

        if not isinstance(submission_rules, list):
            return {}

        expected_submission_type = submission_data.get("expected_submission_type", "")
        allowed_types = set(EXPECTED_SUBMISSION_LABELS.get(expected_submission_type, []))

        for rule in submission_rules:
            if allowed_types and not allowed_types.intersection(rule.get("allowed_submission_types") or []):
                continue

            return {
                "valid_criteria": rule.get("valid_criteria"),
                "invalid_criteria": rule.get("invalid_criteria"),
            }

        return {}

    def _build_prompt_vars(
        self,
        assignment_context: Dict,
        submission_data: Dict,
        rubric_evaluations: Dict,
    ) -> Dict[str, Any]:
        learning_objectives = self.format_objectives(
            assignment_context.get("learning_objectives", [])
        )

        rubric_criteria = self.format_rubrics(
            assignment_context.get("assignment", {}).get("rubrics", "")
        )

        return {
            "assignment_name": assignment_context.get("assignment", {}).get("name", ""),
            "description": assignment_context.get("assignment", {}).get("description", ""),
            "course_vertical": assignment_context.get("assignment", {}).get("course_vertical", ""),
            "submission_guidelines": assignment_context.get("assignment", {}).get("submission_guidelines", ""),
            "assignment_type": assignment_context.get("assignment", {}).get("assignment_type", "Practical"),
            "learning_objectives": learning_objectives,
            "rubric_evaluations": rubric_evaluations,
            "rubric_criteria": rubric_criteria,
            "Language": assignment_context.get("student", {}).get("language", "English"),
            "Grade_Level": assignment_context.get("student", {}).get("grade", "1"),
            "submission_type": submission_data.get("submission_type", ""),
            "submission_text": submission_data.get("submission_text", "") or "",
            "submission_text_context": format_submission_text_for_prompt(submission_data),
            "submission_rules": self._filter_submission_rules(assignment_context, submission_data),
            "expected_submission_type": submission_data.get("expected_submission_type", ""),
            "archetype": submission_data.get("archetype"),
            "current_week": submission_data.get("current_week"),
            "escalation_step_at_submit": submission_data.get("escalation_step_at_submit"),
        }

    def _format_prompts(
        self,
        template: Any,
        assignment_context: Dict,
        submission_data: Dict,
        rubric_evaluations: Dict,
    ) -> Tuple[str, str]:
        try:
            prompt_vars = self._build_prompt_vars(
                assignment_context,
                submission_data,
                rubric_evaluations,
            )
            print("##############")
            print(prompt_vars)
            print("##############")

            system_segment = frappe.get_doc("Prompt Segment", template.system_segment)
            grading_segment = frappe.get_doc("Prompt Segment", template.grading_segment)
            subject_segment = frappe.get_doc("Prompt Segment", template.subject_segment)
            output_segment = frappe.get_doc("Prompt Segment", template.output_segment)

            system_prompt = self._render_prompt_content(system_segment.content, prompt_vars)
            user_prompt_sections = [
                self._render_prompt_content(grading_segment.content, prompt_vars),
                self._render_prompt_content(subject_segment.content, prompt_vars),
                self._render_prompt_content(output_segment.content, prompt_vars),
            ]

            if submission_data.get("submission_type") in TEXT_SUBMISSION_TYPES:
                user_prompt_sections.append(prompt_vars["submission_text_context"])

            formatted_user_prompt = "\n\n".join(section for section in user_prompt_sections if section)
            # print("#"*80)
            # print(formatted_user_prompt)
            # print("#"*80)
            return system_prompt, formatted_user_prompt
        except Exception as e:
            print(f"Error formatting prompt: {e}")
            raise Exception("Failed to assemble prompt with specified variables")

    def _parse_rubric_evaluations(self, raw_text: str) -> Dict:
        cleaned_text = self.clean_json_response(raw_text or "")
        try:
            rubric_evaluations = json.loads(cleaned_text)
            return rubric_evaluations
        except Exception as e:
            print(f"Error parsing rubric evaluations: {e}")
            raise Exception("Failed to parse rubric evaluations from LLM response")
        
    def _parse_grade_value_feedback(self, grade_value) -> int:
        feedback = {
            "overall_feedback": "Good job",
            "overall_feedback_translated": "Good job",
            "final_grade": 0,
            "rubric_evaluations": [
                {
                    "Skill": "Content Knowledge",
                    "grade_value": grade_value,
                    "observation": "Neutral"
                }
            ]
        }
        return feedback

    def _parse_feedback(self, raw_text: str, expected_format: Dict) -> Dict:
        cleaned_text = self.clean_json_response(raw_text or "")
        try:
            feedback = json.loads(cleaned_text)
            feedback = self.feedback_service.validate_feedback_structure(feedback, expected_format)
        except json.JSONDecodeError:
            feedback = self.feedback_service.create_error_feedback(raw_text)

        return feedback

    def _attach_plagiarism_defaults(self, feedback: Dict) -> Dict:
        feedback["plagiarism_output"] = {
            "is_plagiarized": False,
            "is_ai_generated": False,
            "match_type": "original",
            "plagiarism_source": "none",
            "similarity_score": 0.0,
            "ai_detection_source": "none",
            "ai_confidence": 0.0,
            "similar_sources": [],
        }
        return feedback
    
    def _attach_evaluation_to_feedback(self, feedback: Dict, evaluation_result: Dict) -> Dict:
        feedback["rubric_evaluations"] = evaluation_result.get("rubric_evaluations", [])
        return feedback
    
    def _attach_default_fileds(self, feedback: Dict) -> Dict:
        feedback.setdefault("strengths", [])
        feedback.setdefault("areas_for_improvement", [])
        feedback.setdefault("encouragement", "")
        return feedback

    def _template_used_name(self, template: Any) -> str:
        try:
            if hasattr(template, "name"):
                return template.name
        except Exception:
            pass
        return "Built-in Universal Template"

    def _touch_prompt_segment(self, segment_name: str) -> None:
        if not segment_name:
            return
        frappe.db.set_value("Prompt Segment", segment_name, "last_used", datetime.now(), update_modified=False)

    def _eval_provider(self) -> str:
        """Which provider runs the LLM evaluation stage. Defaults to Kaapi;
        set env RAG_EVAL_PROVIDER (e.g. "Anthropic") to roll back."""
        import os
        return os.environ.get("RAG_EVAL_PROVIDER", "Kaapi")

    def _prepare_eval(
        self,
        assignment_context: Dict,
        submission_data: Dict,
        media_type: str,
    ) -> Dict[str, Any]:
        """Everything that happens BEFORE the model call: resolve the provider,
        fetch the prompt template, and render the prompt.

        Returns a plain dict the caller can use to (a) make the model call inline
        (the real-time path) or (b) ship `combined_prompt` (+ image) to Kaapi's
        ASSESSMENT batch API (the nightly submit phase). No model call happens here.
        """
        provider_name = self._eval_provider()
        llm_provider, model_used = self._create_llm_provider(provider_name)

        activity_type = assignment_context["assignment"].get("activity_type")
        course_vertical = assignment_context["assignment"].get("course_vertical")
        if media_type == "text":
            # Preserve the existing text path's hard override.
            course_vertical = "Arts"

        template = self.get_prompt_template(media_type, "both", activity_type, course_vertical)
        expected_format = self._get_expected_format(template)
        system_prompt, formatted_user_prompt = self._format_prompts(
            template,
            assignment_context,
            submission_data,
            [],
        )

        return {
            "provider_name": provider_name,
            "llm_provider": llm_provider,
            "model_used": model_used,
            "template": template,
            "template_used": self._template_used_name(template),
            "expected_format": expected_format,
            "system_prompt": system_prompt,
            "formatted_user_prompt": formatted_user_prompt,
            "combined_prompt": f"{system_prompt}\n\n{formatted_user_prompt}",
        }

    def finalize_feedback(
        self,
        raw_text: str,
        expected_format: Dict,
        cost: Any,
        log_prob: Optional[float] = None,
    ) -> Dict:
        """Everything that happens AFTER the model call: parse the raw output into
        the feedback structure, attach plagiarism defaults + default fields, and set
        the cost/log-prob markers. Shared by the real-time path and the Kaapi webhook
        path (which passes Kaapi's returned JSON as `raw_text`)."""
        feedback = self._parse_feedback(raw_text, expected_format)
        feedback = self._attach_plagiarism_defaults(feedback)
        feedback = self._attach_default_fileds(feedback)
        feedback["strengths"] = [
            f"cost:{cost}",
            f"Feedback_LP:{log_prob if log_prob is not None else 0.0}",
            f"Eval_LP:{0.0}",
        ]
        return feedback

    def _create_llm_provider(
        self,
        llm_provider_name: str = "Gemini",
        model_name: Optional[str] = None,
    ) -> Tuple[Any, str]:
        filters = {"is_active": 1, "provider": llm_provider_name}
        if model_name:
            filters["model_name"] = model_name

        llm_settings = frappe.get_list(
            "LLM Settings",
            filters=filters,
            limit=1,
        )

        if not llm_settings:
            model_msg = f" with model {model_name}" if model_name else ""
            raise Exception(f"No active {llm_provider_name}{model_msg} configuration found")

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

    async def generate_ai_feedback(
        self, assignment_context: Dict, submission_data: Dict, submission_id: str
    ) -> Tuple[Dict, str, str]:
        media_type = self.detect_media_type(submission_data)
        if media_type == "video":
            from .video_evaluation import VideoEvaluationGenerator

            return await VideoEvaluationGenerator(self.feedback_service).generate_feedback(
                assignment_context, submission_data, submission_id
            )

        if media_type == "audio":
            from .audio_evaluation import AudioEvaluationGenerator

            return await AudioEvaluationGenerator(self.feedback_service).generate_feedback(
                assignment_context, submission_data, submission_id
            )

        if media_type == "text":
            from .text_evaluation import TextEvaluationGenerator

            return await TextEvaluationGenerator(self.feedback_service).generate_feedback(
                assignment_context, submission_data, submission_id
            )

        from .image_evaluation_both import ImageEvaluationGenerator

        return await ImageEvaluationGenerator(self.feedback_service).generate_feedback(
            assignment_context, submission_data, submission_id
        )


    # async def generate_ai_feedback(
    #     self, assignment_context: Dict, submission_url: str, submission_id: str
    # ) -> Tuple[Dict, str, str]:

    #     from .video_evaluation import VideoEvaluationGenerator

    #     return await VideoEvaluationGenerator(self.feedback_service).generate_feedback(
    #         assignment_context, submission_url, submission_id
    #     )
