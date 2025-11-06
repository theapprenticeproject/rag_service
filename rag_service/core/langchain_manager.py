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
            print("\n=== Getting Universal Template ===")
            
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
                self.system_prompt = """You are an expert educational feedback assistant that provides constructive, age-appropriate feedback on student submissions across all subjects and assignment types. You adapt your evaluation criteria and language based on the assignment context provided.

CRITICAL: You must ALWAYS respond with valid JSON, never plain text."""

                self.user_prompt = """Assignment Context:
Assignment Name: {assignment_name}
Subject Area: {course_vertical}
Assignment Type: {assignment_type}
Description: {assignment_description}

Learning Objectives:
{learning_objectives}

Please analyze this student submission and provide feedback in the required JSON format."""

                self.response_format = """{
    "overall_feedback": "Comprehensive feedback about the submission",
    "strengths": ["Specific strength 1", "Specific strength 2", "Specific strength 3"],
    "areas_for_improvement": ["Improvement area 1", "Improvement area 2"],
    "learning_objectives_feedback": ["Feedback on learning objective 1"],
    "grade_recommendation": 85,
    "encouragement": "Encouraging message for the student"
}"""
        
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

    def get_default_response_format(self) -> Dict:
        """Get default response format"""
        return {
            "overall_feedback": "Overall assessment of the submission",
            "strengths": ["Strength 1", "Strength 2", "Strength 3"],
            "areas_for_improvement": ["Area 1", "Area 2"],
            "learning_objectives_feedback": ["Feedback on objective 1"],
            "grade_recommendation": 75,
            "encouragement": "Encouraging message for the student"
        }

    async def generate_feedback(self, assignment_context: Dict, submission_url: str, submission_id: str) -> Dict:
        """Generate feedback using universal template approach"""
        try:
            print("\n=== Starting Universal Feedback Generation ===")
            
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
            
            # SIMPLIFIED: Use template directly without complex modifications
            # The universal template handles all subject types internally
            
            # Format user prompt with assignment context
            user_prompt_vars = {
                "assignment_name": assignment_context["assignment"].get("name", ""),
                "assignment_description": assignment_context["assignment"].get("description", ""),
                "course_vertical": assignment_context.get("course_vertical", "General"),
                "assignment_type": assignment_context["assignment"].get("type", "Practical"),
                "learning_objectives": learning_objectives
            }
            
            # Format the user prompt with available variables
            formatted_user_prompt = template.user_prompt
            for key, value in user_prompt_vars.items():
                placeholder = "{" + key + "}"
                if placeholder in formatted_user_prompt:
                    formatted_user_prompt = formatted_user_prompt.replace(placeholder, str(value))

            # Use template system prompt as-is (it already handles JSON requirement)
            system_prompt = template.system_prompt

            # Prepare messages for the LLM provider
            messages = self.llm_provider.format_messages(
                system_prompt=system_prompt,
                user_prompt=formatted_user_prompt,
                image_url=submission_url
            )

            print(f"\nAssignment: {assignment_context['assignment'].get('name', 'Unknown')}")
            print(f"Subject: {assignment_context.get('course_vertical', 'General')}")
            print(f"Type: {assignment_context['assignment'].get('type', 'Unknown')}")
            print("\nSending request to LLM...")
            
            # Generate feedback - SINGLE LLM CALL (no separate validation)
            raw_text = await self.llm_provider.generate_with_vision(messages)
            print(f"\nRaw LLM Response: {raw_text}")

            try:
                # Clean up the response text
                cleaned_text = self.clean_json_response(raw_text)
                print(f"\nCleaned Response Text: {cleaned_text}")
                
                feedback = json.loads(cleaned_text)
                print("\nSuccessfully parsed JSON response")
                
                # Validate and ensure required fields
                feedback = self.validate_feedback_structure(feedback, expected_format)
                
            except json.JSONDecodeError as e:
                print(f"\nJSON Parse Error: {str(e)}")
                print("Using fallback feedback format")
                
                # Create structured fallback response
                feedback = self.create_fallback_feedback(assignment_context, expected_format)

            print("\n=== Feedback Generation Completed Successfully ===")
            return feedback

        except Exception as e:
            error_msg = f"Error generating feedback for submission {submission_id}: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Feedback Generation Error")
            
            # Return structured error response
            return self.create_error_feedback(assignment_context)

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

    def create_fallback_feedback(self, assignment_context: Dict, expected_format: Dict) -> Dict:
        """Create structured fallback when JSON parsing fails"""
        assignment_name = assignment_context["assignment"].get("name", "this assignment")
        
        fallback = {}
        for field, default_value in expected_format.items():
            if field == "overall_feedback":
                fallback[field] = f"I encountered a formatting issue while processing your submission for {assignment_name}. This appears to be a technical problem on our end. Please try resubmitting if this issue persists."
            elif field == "grade_recommendation":
                fallback[field] = 50  # Neutral grade for technical issues
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

    def create_error_feedback(self, assignment_context: Dict) -> Dict:
        """Create feedback for system errors"""
        assignment_name = assignment_context["assignment"].get("name", "this assignment")
        
        return {
            "overall_feedback": f"I encountered a system error while processing your submission for {assignment_name}. This appears to be a technical issue on our end. Please try resubmitting, and if the issue persists, contact your instructor.",
            "strengths": ["Your submission was received successfully"],
            "areas_for_improvement": ["No issues identified with your submission - this appears to be a technical problem"],
            "learning_objectives_feedback": ["Unable to evaluate due to system error - please resubmit"],
            "grade_recommendation": 0,
            "encouragement": "Technical issues don't reflect your effort or ability - please try again!"
        }

    @staticmethod
    def format_feedback_for_display(feedback: Dict) -> str:
        """Format feedback for human-readable display"""
        try:
            formatted = []
            
            if "overall_feedback" in feedback:
                formatted.append("Overall Feedback:")
                formatted.append(feedback["overall_feedback"])
            
            if "strengths" in feedback:
                formatted.append("\nStrengths:")
                for strength in feedback["strengths"]:
                    formatted.append(f"- {strength}")
                    
            if "areas_for_improvement" in feedback:
                formatted.append("\nAreas for Improvement:")
                for area in feedback["areas_for_improvement"]:
                    formatted.append(f"- {area}")
                    
            if "learning_objectives_feedback" in feedback:
                formatted.append("\nLearning Objectives Feedback:")
                for obj in feedback["learning_objectives_feedback"]:
                    formatted.append(f"- {obj}")
                    
            if "grade_recommendation" in feedback:
                formatted.append(f"\nGrade Recommendation: {feedback['grade_recommendation']}")
                
            if "encouragement" in feedback:
                formatted.append(f"\nEncouragement: {feedback['encouragement']}")
            
            return "\n".join(formatted)
            
        except Exception as e:
            error_msg = f"Error formatting feedback: {str(e)}"
            print(f"\nError: {error_msg}")
            return "Error formatting feedback for display. Please check the JSON feedback data."

    def get_current_config(self) -> Dict:
        """Get current LLM configuration"""
        if not self.llm_provider:
            return {"status": "not_configured"}
            
        return {
            "provider": self.llm_provider.__class__.__name__,
            "model": self.llm_provider.model_name,
            "temperature": self.llm_provider.temperature,
            "max_tokens": self.llm_provider.max_tokens
        }
