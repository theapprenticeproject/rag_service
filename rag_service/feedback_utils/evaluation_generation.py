# rag_service/rag_service/feedback_utils/evaluation_generation.py

import json
from datetime import datetime
from typing import Any, Dict, List, Tuple

import frappe

from .image_evaluation import ImageEvaluationGenerator
from .video_evaluation import VideoEvaluationGenerator


class BaseEvaluationGenerator:
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

    def __init__(self, feedback_service: Any):
        self.feedback_service = feedback_service

    def detect_media_type(self, submission_url: str) -> str:
        """Detect media type based on URL/extension."""
        if not submission_url:
            return "image"

        url_without_query = submission_url.split("?", 1)[0].lower()
        if "." in url_without_query:
            ext = url_without_query.rsplit(".", 1)[-1]
            if ext in self.IMAGE_EXTENSIONS:
                return "image"
            if ext in self.VIDEO_EXTENSIONS:
                return "video"

        if "video" in url_without_query:
            return "video"
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
            return "No specific learning objectives provided for this assignment."

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
        for criterion, grades_list in rubrics.items():
            prompt += f"\n{criterion}:\n"
            for grade_item in grades_list:
                prompt += (
                    f"  Grade {grade_item['grade_value']}: {grade_item['grade_description']}\n"
                )

        return prompt

    def get_default_response_format(self) -> Dict:
        """Get default response format."""
        return {
            "rubric_evaluations": [
                {
                    "skill": "Skill Name",
                    "grade_value": 2,
                    "observation": "specific evidence from submission",
                },
                {
                    "skill": "Skill Name",
                    "grade_value": 2,
                    "observation": "specific evidence from submission",
                },
            ],
            "strengths": ["Strength 1", "Strength 2", "Strength 3"],
            "areas_for_improvement": ["Area 1", "Area 2"],
            "encouragement": "Encouraging message for the student",
            "overall_feedback": "Overall assessment of the submission",
            "overall_feedback_translated": "Translation of overall_feedback.",
            "learning_objectives_feedback": ["Feedback on objective 1"],
            "final_grade": 75,
        }

    def get_builtin_template(self):
        """Return built-in default template as fallback."""

        class BuiltinTemplate:
            def __init__(self):
                self.template_name = "Built-in Universal Template"
                self.system_prompt = """You are an encouraging, knowledgeable educational assistant that provides constructive feedback on student submissions using a structured rubric-based evaluation.
                                EVALUATION GUIDELINES: Assess submissions against the provided rubric criteria. For each criterion, determine the appropriate grade level (1-5 scale) based on the rubric descriptions provided. Prioritize growth recognition over perfection.

                                GRADING PHILOSOPHY:
                                - Credit partial mastery: A student showing 60% competency deserves acknowledgment of that progress. Grades need not be binary (good/bad).
                                - Growth mindset framing: Every submission represents learning in progress. Frame gaps as natural and achievable, not deficiencies.
                                - Reserve lower grades (1-2) only for minimal engagement or complete absence of skill demonstration
                                - Lean toward higher grades when effort and authenticity are evident


                                Always provide feedback that is:
                                - Encouraging and positive while being constructive
                                - Age-appropriate and specific to observations
                                - Directly aligned with rubric criteria
                                - Clear about achievement gaps and growth areas

                                Structure your response by:
                                1. Opening with what the student did well (be specific)
                                2. Evaluating the submission against each rubric skill
                                3. Assigning a single grade for each skill in the rubric criteria
                                4. Evaluating only the skills mentioned in the rubric criteria
                                5. Providing specific, actionable feedback for growth
                                6. Ending with motivating encouragement
                                7. Translate the overall_feedback. Translation rules: 
                                    - Formal but friendly tone (customer communication).
                                    - Natural, conversational phrasing. Not literal translation.
                                    - Use native script for the language.

                                CRITICAL: You must respond with valid JSON format only. 
                                """

                self.user_prompt = """Assignment Context:
                            - Name: {assignment_name}
                            - Subject: {course_vertical}
                            - Type: {assignment_type}
                            - Description: {assignment_description}

                            Learning Objectives: {learning_objectives}

                            Rubric Criteria: {rubric_criteria}

                            CRITICAL: It is crucial that the image looks like a photo clicked by a student using a mobile camera. It shouldn't be a digitally created image or one sourced from the internet. Grade it accordingly.

                            Analyze this submission and respond ONLY in this JSON format:

                            {
                                "rubric_evaluations": [
                                    {
                                        "Skill": "Skill Name",
                                        "grade_value": 1-5,
                                        "observation": "specific evidence from submission"
                                    }
                                ],
                                "strengths": ["specific strength 1", "specific strength 2"],
                                "areas_for_improvement": ["actionable suggestion 1", "actionable suggestion 2"],
                                "encouragement": "motivating closing statement",
                                "overall_feedback": "30-50 words of encouraging feedback addressing the student in a friendly tone that summarises strengths and improvement potential. Or 'Submission does not match assignment requirements.'",
                                "overall_feedback_translated": "Translation of overall_feedback in {Language} for a Grade {Grade_Level} student as per translation rules.",
                                "learning_objectives_feedback": ["Feedback on objective 1",],
                                "final_grade": "average of all rubric grades (0-5 scale, converted to 0-100)"
                                
                            }
                            """

                self.response_format = """{
                                        "rubric_evaluations": [
                                            {
                                            "skill": "Skill Name",
                                            "grade_value": 2,
                                            "observation": "specific evidence from submission"
                                            },
                                            {
                                            "skill": "Skill Name",
                                            "grade_value": 2,
                                            "observation": "specific evidence from submission"
                                            }
                                        ],
                                        "strengths": ["Strength 1", "Strength 2", "Strength 3"],
                                        "areas_for_improvement": ["Area 1", "Area 2"],
                                        "encouragement": "Encouraging message for the student",
                                        "overall_feedback": "Overall assessment of the submission",
                                        "overall_feedback_translated": "Translation of overall_feedback.",
                                        "learning_objectives_feedback": ["Feedback on objective 1",],
                                        "final_grade": 75,
                                        }
                                    """

        return BuiltinTemplate()

    def get_prompt_template(self, media_type: str):
        """Get active template for the given media type."""
        try:
            print("\n=== Getting Prompt Template ===")

            templates = frappe.get_list(
                "Prompt Template",
                filters={"is_active": 1, "media_type": media_type},
                order_by="version desc",
                limit=1,
            )

            if templates:
                template = frappe.get_doc("Prompt Template", templates[0].name)
                print(f"Using {media_type} template: {template.template_name}")

                template.db_set("last_used", datetime.now())
                frappe.db.commit()
                return template

            print("No active template found, using built-in default")
            return self.get_builtin_template()

        except Exception as e:
            error_msg = f"Template Error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Template Error")
            return self.get_builtin_template()

    def _get_expected_format(self, template: Any) -> Dict:
        try:
            if hasattr(template, "response_format") and template.response_format:
                return json.loads(template.response_format)
        except json.JSONDecodeError:
            pass
        return self.get_default_response_format()

    def _format_user_prompt(self, template: Any, assignment_context: Dict, media_type: str) -> str:
        learning_objectives = self.format_objectives(
            assignment_context.get("learning_objectives", [])
        )
        rubric_criteria = self.format_rubrics(
            assignment_context.get("assignment", {}).get("rubrics", "")
        )

        user_prompt_vars = {
            "assignment_name": assignment_context.get("assignment", {}).get("name", ""),
            "assignment_description": assignment_context.get("assignment", {}).get("description", ""),
            "course_vertical": assignment_context.get("subject", "General"),
            "assignment_type": assignment_context.get("assignment", {}).get("type", "Practical"),
            "learning_objectives": learning_objectives,
            "rubric_criteria": rubric_criteria,
            "Language": assignment_context.get("student", {}).get("language", "English"),
            "Grade_Level": assignment_context.get("student", {}).get("grade", "1"),
        }

        formatted_user_prompt = template.user_prompt
        for key, value in user_prompt_vars.items():
            placeholder = "{" + key + "}"
            if placeholder in formatted_user_prompt:
                formatted_user_prompt = formatted_user_prompt.replace(placeholder, str(value))

        if media_type == "video":
            formatted_user_prompt += (
                "\n\nThis submission is a VIDEO. Evaluate the student's work shown in the video. "
                "Ignore cinematography or editing. If the work is unclear, request resubmission."
            )

        return formatted_user_prompt

    def _parse_feedback(self, raw_text: str, expected_format: Dict) -> Dict:
        cleaned_text = self.clean_json_response(raw_text or "")
        try:
            feedback = json.loads(cleaned_text)
            feedback = self.feedback_service.validate_feedback_structure(feedback, expected_format)
        except json.JSONDecodeError:
            feedback = self.feedback_service.create_fallback_feedback(expected_format)

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

    def _template_used_name(self, template: Any) -> str:
        try:
            if hasattr(template, "name"):
                return template.name
        except Exception:
            pass
        return "Built-in Universal Template"


class EvaluationGenerator(BaseEvaluationGenerator):
    """Generate AI feedback for different media types."""

    def __init__(self, feedback_service: Any):
        super().__init__(feedback_service)
        self.image_generator = ImageEvaluationGenerator(feedback_service)
        self.video_generator = VideoEvaluationGenerator(feedback_service)

    async def generate_ai_feedback(
        self, assignment_context: Dict, submission_url: str, submission_id: str
    ) -> Tuple[Dict, str, str]:
        media_type = self.detect_media_type(submission_url)
        if media_type == "video":
            return await self.video_generator.generate_feedback(
                assignment_context, submission_url, submission_id
            )
        return await self.image_generator.generate_feedback(
            assignment_context, submission_url, submission_id
        )
