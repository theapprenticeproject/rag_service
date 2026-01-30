# rag_service/rag_service/core/langchain_manager.py

import frappe
import json
from typing import Dict, List, Optional, Union
from datetime import datetime
from .llm_providers import create_llm_provider, OpenAIProvider

class LangChainManager:
    def __init__(self):
        self.llm = None
        self.llm_provider = None
        self.setup_llm()
        
    def setup_llm(self):
        """Initialize LLM based on settings"""
        try:
            llm_settings = frappe.get_list(
                "LLM Settings",
                filters={"is_active": 1},
                limit=1
            )
            
            if not llm_settings:
                raise Exception("No active LLM configuration found")
                
            settings = frappe.get_doc("LLM Settings", llm_settings[0].name)
            self.model_used = llm_settings[0].name
            print("\nUsing LLM Settings:")
            print(f"Provider: {settings.provider}")
            print(f"Model: {settings.model_name}")
            
            
            # Create LLM provider based on settings
            self.llm_provider = create_llm_provider(
                provider=settings.provider,
                api_key=settings.get_password('api_secret'),
                model_name=settings.model_name,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens
            )
            
            # Keep the llm reference for backward compatibility with OpenAI
            if isinstance(self.llm_provider, OpenAIProvider):
                self.llm = self.llm_provider.llm
                
        except Exception as e:
            error_msg = f"LLM Setup Error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "LLM Setup Error")
            raise

    def clean_json_response(self, response: str) -> str:
        """Clean JSON response from various formats"""
        try:
            # Remove markdown code blocks if present
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                code_blocks = response.split("```")
                if len(code_blocks) >= 3:  # At least one code block exists
                    response = code_blocks[1].strip()
                    # Check if the extracted content looks like JSON
                    if not (response.startswith('{') or response.startswith('[')):
                        # If not, try to find JSON in the original response
                        json_start = response.find('{')
                        if json_start >= 0:
                            response = response[json_start:]
                            
            # Try to extract JSON if response starts with explanation
            if not response.strip().startswith('{'):
                json_start = response.find('{')
                if json_start >= 0:
                    response = response[json_start:]
            
            # Check if the response ends properly
            if not response.strip().endswith('}'):
                json_end = response.rfind('}')
                if json_end >= 0:
                    response = response[:json_end+1]
                    
            return response.strip()
        except Exception as e:
            print(f"Error cleaning JSON: {str(e)}")
            return response

    def get_universal_template(self) -> Dict:
        """Get any active template - no assignment_type filtering"""
        try:
            print("\n=== Getting Prompt Template ===")
            
            # REMOVED: assignment_type filtering - get ANY active template
            templates = frappe.get_list(
                "Prompt Template",
                filters={"is_active": 1},
                order_by="version desc",
                limit=1
            )
            
            if templates:
                template = frappe.get_doc("Prompt Template", templates[0].name)
                print(f"Using universal template: {template.template_name}")
                
                # Update the last_used timestamp
                template.db_set('last_used', datetime.now())
                frappe.db.commit()
                
                return template
            else:
                print("No active template found, using built-in default")
                return self.get_builtin_template()
                
        except Exception as e:
            error_msg = f"Template Error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Template Error")
            return self.get_builtin_template()

    def get_builtin_template(self):
        """Return built-in default template as fallback"""
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

    def format_objectives(self, objectives: List[Dict]) -> str:
        """Format learning objectives for prompt"""
        if not objectives:
            return "No specific learning objectives provided for this assignment."
            
        formatted = []
        for i, obj in enumerate(objectives, 1):
            if isinstance(obj, dict):
                description = obj.get('description', obj.get('objective_id', 'Unknown objective'))
            else:
                description = str(obj)
            formatted.append(f"{i}. {description}")
        
        return "\n".join(formatted)

    def format_rubrics(self, rubrics) -> str:
        prompt = ""
        if isinstance(rubrics, str):
            rubrics = json.loads(rubrics)
        for criterion, grades_list in rubrics.items():
            prompt += f"\n{criterion}:\n"
            for grade_item in grades_list:
                prompt += f"  Grade {grade_item['grade_value']}: {grade_item['grade_description']}\n"
        
        return prompt
    
    def get_default_response_format(self) -> Dict:
        """Get default response format"""
        return {
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
                "learning_objectives_feedback": ["Feedback on objective 1"],
                "final_grade": 75
                }
    
    async def generate_ai_evaluated_feedback(self, assignment_context: Dict, submission_url: str, submission_id: str) -> Dict:
        """Generate feedback using universal template approach"""
        try:
            print("\n=== Starting AI Feedback Generation ===")
            
            # Get universal template (no assignment_type filtering)
            template = self.get_universal_template()
            print("Template loaded successfully")

            # Get expected response format from template or use default
            try:
                if hasattr(template, 'response_format') and template.response_format:
                    expected_format = json.loads(template.response_format)
                    print("Using template-defined response format")
                else:
                    expected_format = self.get_default_response_format()
                    print("Using default response format")
            except json.JSONDecodeError:
                expected_format = self.get_default_response_format()
                print("Failed to parse template response format, using default")

            # Format learning objectives
            learning_objectives = self.format_objectives(assignment_context.get("learning_objectives", []))
            rubric_criteria = self.format_rubrics(assignment_context["assignment"].get("rubrics", ""))

            # Format user prompt with assignment context
            user_prompt_vars = {
                "assignment_name": assignment_context["assignment"].get("name", ""),
                "assignment_description": assignment_context["assignment"].get("description", ""),
                "course_vertical": assignment_context.get("subject", "General"),
                "assignment_type": assignment_context["assignment"].get("type", "Practical"),
                "learning_objectives": learning_objectives,
                "rubric_criteria": rubric_criteria,
                "Language": assignment_context["student"].get("language", "English"),
                "Grade_Level": assignment_context["student"].get("grade", "1")
            }
            
            # Format the user prompt with available variables
            formatted_user_prompt = template.user_prompt
            for key, value in user_prompt_vars.items():
                placeholder = "{" + key + "}"
                if placeholder in formatted_user_prompt:
                    formatted_user_prompt = formatted_user_prompt.replace(placeholder, str(value))

            # Use template system prompt as-is (it already handles JSON requirement)
            system_prompt = template.system_prompt

            print("User Prompt Prepared:")
            print(formatted_user_prompt)

            # Prepare messages for the LLM provider
            messages = self.llm_provider.format_messages(
                system_prompt=system_prompt,
                user_prompt=formatted_user_prompt,
                image_url=submission_url
            )
            print("\nSending request to LLM...")
            
            try:
                # Generate feedback - SINGLE LLM CALL (no separate validation)
                raw_text = await self.llm_provider.generate_with_vision(messages)
                print(f"\nRaw LLM Response: {raw_text}")
                # Clean up the response text
                cleaned_text = self.clean_json_response(raw_text)
                feedback = json.loads(cleaned_text)
                print("\nSuccessfully parsed JSON response")
                
                # Validate and ensure required fields
                feedback = self.validate_feedback_structure(feedback, expected_format)
                
            except json.JSONDecodeError as e:
                print(f"\nJSON Parse Error: {str(e)}")
                print("Using fallback feedback format")
                
                # Create structured fallback response
                feedback = self.create_fallback_feedback(expected_format)

            # Attach default plagiarism/AI-detection metadata
            plagiarism_output = {
                "is_plagiarized": False,
                "is_ai_generated": False,
                "match_type": "original",
                "plagiarism_source": "none",
                "similarity_score": 0.0,
                "ai_detection_source": "none",
                "ai_confidence": 0.0,
                "similar_sources": []
            }
            feedback["plagiarism_output"] = plagiarism_output

            try:
                if hasattr(template, 'name'):
                    template_used = template.name
                else:
                    template_used = "Built-in Universal Template"

            except Exception as template_error:
                print("Used Default Template:")
                template_used = "Built-in Universal Template"
                # Don't fail the entire process for template tracking issues

            print("\n=== Feedback Generation Completed Successfully ===")
            return feedback, template_used

        except Exception as e:
            error_msg = f"Error generating feedback for submission {submission_id}: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Feedback Generation Error")
            
            # Return structured error response
            template_used = "Built-in Universal Template for Error"
            return self.create_error_feedback(), template_used

    async def generate_feedback( self, assignment_context: Dict, submission_url: str, submission_id: str,
                                    plagiarism_data: Dict = None, feedback_request_id: str = None) -> Dict:
        """Generate feedback with plagiarism context"""

        result_status = "Pending"

        try:
            # Check for plagiarism/AI-generated content first
            if plagiarism_data:
                is_plagiarized = plagiarism_data.get("is_plagiarized", False)
                is_ai_generated = plagiarism_data.get("is_ai_generated", False)
                match_type = plagiarism_data.get("match_type", "original")

                # Handle AI-generated submissions
                if is_ai_generated:
                    result_status = "Success - Flagged"
                    feedback = self._create_ai_generated_feedback(
                        plagiarism_data
                    )
                    tempalate_used = "Feedback Template for AI Generated Submission"

                # Handle plagiarized submissions
                elif is_plagiarized and match_type in ["exact_duplicate", "near_duplicate"]:
                    result_status = "Success - Flagged"
                    feedback = self._create_plagiarism_feedback(
                        plagiarism_data
                    )
                    tempalate_used = "Feedback Template for Plagiarized Submission"
                
                # Continue with normal feedback generation for original work
                else:
                    result_status = "Success - Original"
                    feedback, tempalate_used = await self.generate_ai_evaluated_feedback(assignment_context, submission_url,submission_id)
            
            feedback["translation_language"] = assignment_context["student"].get("language", "English")
            await self._update_result_status(feedback_request_id, result_status)
            return feedback, self.model_used, tempalate_used

        except Exception as e:
            result_status = "Failed"
            await self._update_result_status(feedback_request_id, result_status, str(e))
            raise

    async def _update_result_status(self, feedback_request_id: str, status: str, error_message: str = None):
        """Update Feedback Request result_status"""
        if not feedback_request_id:
            return

        update_data = {"result_status": status}
        if error_message:
            update_data["error_message"] = error_message[:500]  # Truncate long errors

        frappe.db.set_value(
            "Feedback Request",
            feedback_request_id,
            update_data,
            update_modified=True
        )
        frappe.db.commit()

    def _create_ai_generated_feedback(self, plagiarism_data: Dict) -> Dict:
        """Create feedback for AI-generated submissions"""

        ai_source = plagiarism_data.get("ai_detection_source", "unknown")
        ai_confidence = plagiarism_data.get("ai_confidence", 0.0)
        response = {
            "overall_feedback": "Your submission appears to be generated by an AI tool. \
            At MentorMe, we encourage original creative work that reflects your own learning \
            and artistic development. AI-generated images, while interesting, don't demonstrate \
            the skills and creativity we're looking to nurture. Please submit your own original \
            artwork for this assignment.",
            "overall_feedback_translated": "Your submission appears to be generated by an AI tool. \
            At MentorMe, we encourage original creative work that reflects your own learning \
            and artistic development. AI-generated images, while interesting, don't demonstrate \
            the skills and creativity we're looking to nurture. Please submit your own original \
            artwork for this assignment.",
            "strengths": ["N/A - AI-generated content detected."],
            "areas_for_improvement": ["Submit original artwork created by you",
                                      "Review assignment guidelines for creative direction"],
            "learning_objectives_feedback": ["N/A - AI-generated content detected."],
            "grade_recommendation": 0,
            "encouragement": "We believe in your creative abilities!",
            "rubric_evaluations": [{
                                        "Skill": "Content Knowledge",
                                        "grade_value": 0,
                                        "observation": "N/A - AI-generated content detected."
                                    }],
            "plagiarism_output": {
                "is_plagiarized": False,
                "is_ai_generated": True,
                "match_type": "ai_generated",
                "plagiarism_source": "none",
                "similarity_score": 0.0,
                "ai_detection_source": ai_source,
                "ai_confidence": ai_confidence,
            }
        }

        return response

    def _create_plagiarism_feedback( self, plagiarism_data: Dict) -> Dict:
        """Create feedback for plagiarized submissions"""

        match_type = plagiarism_data.get("match_type")
        plagiarism_source = plagiarism_data.get("plagiarism_source")
        similarity_score = plagiarism_data.get("similarity_score", 0.0)
        ai_confidence = plagiarism_data.get("ai_confidence", 0.0)

        # respond with structured feedback
        response = {
            "overall_feedback": "Your submission has been flagged for similarity. \
                Academic integrity is fundamental to the learning process. Please ensure your \
                submissions represent your own original work.",
            "overall_feedback_translated": "Your submission has been flagged for similarity. \
                Academic integrity is fundamental to the learning process. Please ensure your \
                submissions represent your own original work.",
            "strengths": ["N/A - Submission flagged for similarity"],
            "areas_for_improvement": ["Create original artwork for this assignment",
                                      "Review academic integrity guidelines"],
            "learning_objectives_feedback": ["N/A - Submission flagged for similarity"],
            "grade_recommendation": 0,
            "encouragement": "Every artist develops their unique style through practice!",
            "rubric_evaluations": [{
                                        "Skill": "Content Knowledge",
                                        "grade_value": 0,
                                        "observation": "N/A - Submission flagged for similarity."
                                    }],
            "plagiarism_output": {
                "is_plagiarized": True,
                "is_ai_generated": False,
                "match_type": match_type,
                "plagiarism_source": plagiarism_source,
                "similarity_score": similarity_score,
                "ai_detection_source": "none",
                "ai_confidence": ai_confidence,
            }
        }

        return response 

    def validate_feedback_structure(self, feedback: Dict, expected_format: Dict) -> Dict:
        """Ensure feedback has all required fields with correct types"""
        # Ensure all expected fields are present
        for field in expected_format:
            if field not in feedback:
                if isinstance(expected_format[field], list):
                    feedback[field] = ["No information provided"]
                elif isinstance(expected_format[field], (int, float)):
                    feedback[field] = 0
                else:
                    feedback[field] = "No information provided"
        
        # Validate grade_recommendation format for TAP LMS compatibility
        try:
            grade = feedback.get("grade_recommendation", 0)
            if isinstance(grade, str):
                # Extract numeric part only
                grade_clean = ''.join(c for c in grade if c.isdigit() or c == '.')
                grade = float(grade_clean) if grade_clean else 0
            feedback["grade_recommendation"] = max(0, min(100, float(grade)))
        except (ValueError, TypeError):
            feedback["grade_recommendation"] = 0
        
        # Ensure list fields are lists
        list_fields = ["strengths", "areas_for_improvement", "learning_objectives_feedback"]
        for field in list_fields:
            if field in feedback and not isinstance(feedback[field], list):
                feedback[field] = [str(feedback[field])]
        
        return feedback

    def create_fallback_feedback(self, expected_format: Dict) -> Dict:
        """Create structured fallback when JSON parsing fails"""
        
        fallback = {}
        for field, default_value in expected_format.items():
            if field == "overall_feedback":
                fallback[field] = "I encountered a system error while processing your submission. This appears to be a technical issue on our end. Please try resubmitting, and if the issue persists, contact your instructor."
            elif field == "overall_feedback_translated":
                fallback[field] = "I encountered a system error while processing your submission. This appears to be a technical issue on our end. Please try resubmitting, and if the issue persists, contact your instructor."
            elif field == "grade_recommendation":
                fallback[field] = 50  # Neutral grade for technical issues
            elif field == "rubric_evaluations":
                fallback[field] = [
                {
                "skill": "Content Knowledge",
                "grade_value": 2,
                "observation": "Neutral evaluation due to processing issue"
                }
            ]
            elif isinstance(default_value, list):
                if "strength" in field:
                    fallback[field] = ["Your submission was received and processed"]
                elif "improvement" in field:
                    fallback[field] = ["Please ensure your submission clearly shows your work"]
                else:
                    fallback[field] = ["Unable to provide specific feedback due to processing issue"]
            else:
                if field == "encouragement":
                    fallback[field] = "Technical issues don't reflect your effort - please try resubmitting!"
                else:
                    fallback[field] = "Processing issue - please resubmit for detailed feedback"
        
        return fallback

    def create_error_feedback(self) -> Dict:
        """Create feedback for system errors"""

        feedback = {
            "overall_feedback": "I encountered a system error while processing your submission. This appears to be a technical issue on our end. Please try resubmitting, and if the issue persists, contact your instructor.",
            "overall_feedback_translated": "I encountered a system error while processing your submission. This appears to be a technical issue on our end. Please try resubmitting, and if the issue persists, contact your instructor.",
            "strengths": ["Your submission was received successfully"],
            "areas_for_improvement": ["No issues identified with your submission - this appears to be a technical problem"],
            "learning_objectives_feedback": ["Unable to evaluate due to system error - please resubmit"],
            "grade_recommendation": 0,
            "encouragement": "Technical issues don't reflect your effort or ability - please try again!",
            "rubric_evaluations": [{
                                        "Skill": "Content Knowledge",
                                        "grade_value": 2,
                                        "observation": "Neutral evaluation due to processing issue"
                                    }],
        }
        plagiarism_output = {
                "is_plagiarized": False,
                "is_ai_generated": False,
                "match_type": "original",
                "plagiarism_source": "none",
                "similarity_score": 0.0,
                "ai_detection_source": "none",
                "ai_confidence": 0.0,
                "similar_sources": []
            }
        feedback["plagiarism_output"] = plagiarism_output
        
        return feedback

