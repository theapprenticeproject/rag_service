# rag_service/rag_service/feedback_utils/evaluation_generation.py

import json
from datetime import datetime
from typing import Any, Dict, List, Tuple

import frappe

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
        # if "Creativity" in rubrics:
        #         del rubrics["Creativity"]
        for criterion, grades_list in rubrics.items():
            prompt += f"\n{criterion}:\n"
            for grade_item in grades_list:
                prompt += (
                    f"  Grade {grade_item['grade_value']}: {grade_item['grade_description']}\n"
                )

        return prompt

    # def get_default_response_format(self) -> Dict:
    #     """Get default response format."""
    #     return {
    #         "rubric_evaluations": [
    #             {
    #                 "skill": "Skill Name",
    #                 "grade_value": 2,
    #                 "observation": "specific evidence from submission",
    #             },
    #             {
    #                 "skill": "Skill Name",
    #                 "grade_value": 2,
    #                 "observation": "specific evidence from submission",
    #             },
    #         ],
    #         "strengths": ["Strength 1", "Strength 2", "Strength 3"],
    #         "areas_for_improvement": ["Area 1", "Area 2"],
    #         "encouragement": "Encouraging message for the student",
    #         "overall_feedback": "Overall assessment of the submission",
    #         "overall_feedback_translated": "Translation of overall_feedback.",
    #         "learning_objectives_feedback": ["Feedback on objective 1"],
    #         "final_grade": 75,
    #     }

    # def get_default_evaluation_response_format(self) -> Dict:
    #     """Get default response format for rubric-only evaluation."""
    #     return {
    #         "rubric_evaluations": [
    #             {
    #                 "skill": "Skill Name",
    #                 "grade_value": 2,
    #                 "observation": "specific evidence from submission",
    #             },
    #             {
    #                 "skill": "Skill Name",
    #                 "grade_value": 2,
    #                 "observation": "specific evidence from submission",
    #             },
    #         ]
    #     }

    def get_prompt_template(self, media_type: str, prompt_type: str, activity_type: str, course_vertical: str):
        """Get active template for the given media type."""
        try:
            print(f"\n=== Getting Prompt Template for {prompt_type}===")

            # print(f"Media Type: {media_type}")
            # print(f"Course Vertical: {course_vertical}")
            # print(f"prompt_type Type: {prompt_type}")
            # print(f"activity_type: {activity_type}")
            # import sys
            # sys.exit(0)


            templates = frappe.get_list(
                "Prompt Template",
                filters={"is_active": 1, "media_type": media_type, "prompt_type": prompt_type, 
                         "activity_type": activity_type, "course_vertical": course_vertical },
                order_by="version desc",
                limit=1,
            )

            template = frappe.get_doc("Prompt Template", templates[0].name)
            print(f"Using {media_type} {prompt_type} template: {template.template_name}")

            template.db_set("last_used", datetime.now())
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


    def _format_user_prompt(
        self,
        template: Any,
        assignment_context: Dict,
        media_type: str,
        rubric_evaluations: Dict,
    ) -> str:
        try:
            learning_objectives = self.format_objectives(
                assignment_context.get("learning_objectives", [])
            )
            
            rubric_criteria = self.format_rubrics(assignment_context.get("assignment", {}).get("rubrics", ""))
            

            # print(rubric_criteria)

            user_prompt_vars = {
                "assignment_name": assignment_context.get("assignment", {}).get("name", ""),
                "assignment_description": assignment_context.get("assignment", {}).get("description", ""),
                "course_vertical": assignment_context.get("course_vertical", "General"),
                "assignment_type": assignment_context.get("assignment", {}).get("assignment_type", "Practical"),
                "learning_objectives": learning_objectives,
                "rubric_evaluations": rubric_evaluations,
                "rubric_criteria": rubric_criteria,
                "Language": assignment_context.get("student", {}).get("language", "English"),
                "Grade_Level": assignment_context.get("student", {}).get("grade", "1"),
            }

            formatted_user_prompt = template.user_prompt
            for key, value in user_prompt_vars.items():
                placeholder = "{" + key + "}"
                if placeholder in formatted_user_prompt:
                    formatted_user_prompt = formatted_user_prompt.replace(placeholder, str(value))

            return formatted_user_prompt
        except Exception as e:
            print(f"Error formatting user prompt: {e}")
            raise Exception("Failed to format user prompt with specified variables")

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
            "grade_recommendation": 0,
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

    async def generate_ai_feedback(
        self, assignment_context: Dict, submission_url: str, submission_id: str
    ) -> Tuple[Dict, str, str]:
        media_type = self.detect_media_type(submission_url)
        if media_type == "video":
            from .video_evaluation import VideoEvaluationGenerator

            return await VideoEvaluationGenerator(self.feedback_service).generate_feedback(
                assignment_context, submission_url, submission_id
            )

        from .image_evaluation_both import ImageEvaluationGenerator

        return await ImageEvaluationGenerator(self.feedback_service).generate_feedback(
            assignment_context, submission_url, submission_id
        )


    # async def generate_ai_feedback(
    #     self, assignment_context: Dict, submission_url: str, submission_id: str
    # ) -> Tuple[Dict, str, str]:

    #     from .video_evaluation import VideoEvaluationGenerator

    #     return await VideoEvaluationGenerator(self.feedback_service).generate_feedback(
    #         assignment_context, submission_url, submission_id
    #     )
