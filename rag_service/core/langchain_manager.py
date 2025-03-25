# rag_service/rag_service/core/langchain_manager.py

import frappe
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
import json
from typing import Dict, List, Optional, Union
import httpx
from datetime import datetime

class LangChainManager:
    def __init__(self):
        self.llm = None
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
            
            if settings.provider == "OpenAI":
                self.llm = ChatOpenAI(
                    model_name=settings.model_name,
                    openai_api_key=settings.get_password('api_secret'),
                    temperature=settings.temperature,
                    max_tokens=settings.max_tokens
                )
            elif settings.provider == "Anthropic":
                # Add Anthropic model initialization if needed
                raise Exception("Anthropic provider not yet implemented")
            else:
                raise Exception(f"Unsupported LLM provider: {settings.provider}")
                
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

    async def validate_submission_image(self, image_url: str, assignment_type: str) -> Dict:
        """Pre-validate if image appears to be appropriate for the assignment type"""
        try:
            print(f"\n=== Validating Submission Image ===")
            print(f"URL: {image_url}")
            print(f"Assignment Type: {assignment_type}")

            validation_prompt = f"""You are an artwork submission validator.
Analyze the image and determine if it is a valid submission for a {assignment_type} assignment.
You must respond ONLY with a JSON object containing these exact fields:
{{
    "is_valid": boolean,
    "reason": "detailed explanation of why the image is valid or invalid",
    "detected_type": "specific description of what type of image this appears to be"
}}"""

            messages = [
                SystemMessage(content=validation_prompt),
                HumanMessage(content=[{
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }])
            ]

            response = await self.llm.agenerate([messages])
            result = response.generations[0][0].text.strip()
            print(f"\nRaw Validation Response: {result}")
            
            # Clean and parse the response
            cleaned_result = self.clean_json_response(result)
            print(f"\nCleaned Validation Response: {cleaned_result}")
            
            try:
                validation_result = json.loads(cleaned_result)
                print(f"\nParsed Validation Result: {json.dumps(validation_result, indent=2)}")
                return validation_result
            except json.JSONDecodeError as e:
                print(f"JSON Decode Error: {str(e)}")
                return {
                    "is_valid": True,  # Default to True to avoid false negatives
                    "reason": "Failed to validate image format, proceeding with analysis",
                    "detected_type": "unvalidated_submission"
                }

        except Exception as e:
            error_msg = f"Image validation failed: {str(e)}"
            print(f"\nError: {error_msg}")
            return {
                "is_valid": True,  # Default to True to avoid false negatives
                "reason": error_msg,
                "detected_type": "error_during_validation"
            }

    def format_objectives(self, objectives: List[Dict]) -> str:
        """Format learning objectives for prompt"""
        if not objectives:
            return "No specific learning objectives provided for this assignment."
            
        return "\n".join([
            f"- {obj.get('description', obj.get('objective_id', 'Unknown objective'))}" 
            for obj in objectives
        ])

    async def get_image_content(self, image_url: str) -> Dict:
        """Get image content in format required by GPT-4V"""
        print(f"\nPreparing image content from URL: {image_url}")
        return {
            "type": "image_url",
            "image_url": {
                "url": image_url,
                "detail": "high"
            }
        }

    def get_prompt_template(self, assignment_type: str) -> Dict:
        """Get active prompt template for assignment type"""
        try:
            # First try to get an exact match for the assignment type
            templates = frappe.get_list(
                "Prompt Template",
                filters={
                    "assignment_type": assignment_type,
                    "is_active": 1
                },
                order_by="version desc",
                limit=1
            )
            
            # If no exact match found, try to get a generic template
            if not templates:
                print(f"\nNo specific template found for {assignment_type}, looking for generic template")
                templates = frappe.get_list(
                    "Prompt Template",
                    filters={
                        "is_active": 1
                    },
                    order_by="version desc",
                    limit=1
                )
                
            if not templates:
                raise Exception(f"No active prompt template found for {assignment_type}")
                
            template = frappe.get_doc("Prompt Template", templates[0].name)
            print(f"\nUsing template: {template.template_name}")
            
            # Update the last_used timestamp
            template.db_set('last_used', datetime.now())
            frappe.db.commit()
            
            return template
            
        except Exception as e:
            error_msg = f"Prompt Template Error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Prompt Template Error")
            raise

    def get_default_response_format(self, assignment_type: str) -> Dict:
        """Get default response format if template doesn't define one"""
        # Default format based on assignment type
        default_formats = {
            "Written": {
                "overall_feedback": "Overall assessment of the written work",
                "strengths": ["Strength 1", "Strength 2"],
                "areas_for_improvement": ["Area 1", "Area 2"],
                "learning_objectives_feedback": ["Feedback on objective 1"],
                "grade_recommendation": "Numerical grade",
                "encouragement": "Encouraging message for the student"
            },
            "Practical": {
                "overall_feedback": "Overall assessment of the practical work",
                "strengths": ["Strength 1", "Strength 2"],
                "areas_for_improvement": ["Area 1", "Area 2"],
                "learning_objectives_feedback": ["Feedback on objective 1"],
                "grade_recommendation": "Numerical grade",
                "encouragement": "Encouraging message for the student"
            },
            "Performance": {
                "overall_feedback": "Overall assessment of the performance",
                "strengths": ["Strength 1", "Strength 2"],
                "areas_for_improvement": ["Area 1", "Area 2"],
                "learning_objectives_feedback": ["Feedback on objective 1"],
                "grade_recommendation": "Numerical grade",
                "encouragement": "Encouraging message for the student"
            },
            "Collaborative": {
                "overall_feedback": "Overall assessment of the collaborative work",
                "strengths": ["Strength 1", "Strength 2"],
                "areas_for_improvement": ["Area 1", "Area 2"],
                "learning_objectives_feedback": ["Feedback on objective 1"],
                "grade_recommendation": "Numerical grade",
                "encouragement": "Encouraging message for the student"
            }
        }
        
        # Return type-specific format or a generic one
        return default_formats.get(assignment_type, default_formats["Practical"])

    async def generate_feedback(self, assignment_context: Dict, submission_url: str, submission_id: str) -> Dict:
        """Generate feedback using LangChain and GPT-4V"""
        try:
            print("\n=== Starting Feedback Generation ===")
            
            # Get assignment type from context
            assignment_type = assignment_context["assignment"]["type"]
            
            # Get prompt template based on assignment type
            template = self.get_prompt_template(assignment_type)
            print("\nTemplate loaded successfully")

            # Get expected response format from template or use default
            try:
                if hasattr(template, 'response_format') and template.response_format:
                    expected_format = json.loads(template.response_format)
                    print("\nUsing template-defined response format")
                else:
                    expected_format = self.get_default_response_format(assignment_type)
                    print("\nUsing default response format for assignment type:", assignment_type)
            except json.JSONDecodeError:
                expected_format = self.get_default_response_format(assignment_type)
                print("\nFailed to parse template response format, using default")

            # Format expected format as JSON string
            response_format_str = json.dumps(expected_format, indent=2)
            
            # Validate image first (keep this generic)
            validation_result = await self.validate_submission_image(
                submission_url, 
                assignment_type
            )

            # Process based on validation result
            if validation_result.get("is_valid", False):
                print("\nValid submission detected - generating feedback")
                
                # Format learning objectives if they exist
                learning_objectives = ""
                if assignment_context.get("learning_objectives") and len(assignment_context["learning_objectives"]) > 0:
                    learning_objectives = self.format_objectives(assignment_context["learning_objectives"])
                
                # Prepare system prompt with expected format
                enhanced_system_prompt = f"""
                {template.system_prompt}
                
                IMPORTANT: You must ALWAYS respond with a valid JSON object matching this format exactly:
                {response_format_str}
                
                If the image does not appear to be related to this assignment context, set the "overall_feedback" field to EXACTLY:
                "Something went wrong—It looks like there's an issue from our end or your submission is incorrect! I am not able to provide feedback for your submission."
                
                Do not include any additional text, explanation, or markdown formatting outside the JSON object.
                Return ONLY the JSON object, nothing else.
                """

                # Format variables for the user prompt
                user_prompt_vars = {
                    "assignment_description": assignment_context["assignment"]["description"],
                    "learning_objectives": learning_objectives,
                    "assignment_type": assignment_type,
                    "assignment_name": assignment_context["assignment"]["name"]
                }
                
                # Try to apply any additional variables from the template
                if hasattr(template, 'variables') and template.variables:
                    for var in template.variables:
                        if var.variable_name in assignment_context:
                            user_prompt_vars[var.variable_name] = assignment_context[var.variable_name]
                
                # Format the user prompt with available variables
                formatted_user_prompt = template.user_prompt
                for key, value in user_prompt_vars.items():
                    placeholder = "{" + key + "}"
                    if placeholder in formatted_user_prompt:
                        formatted_user_prompt = formatted_user_prompt.replace(placeholder, str(value))

                # Prepare text content
                text_content = {
                    "type": "text",
                    "text": formatted_user_prompt
                }

                # Prepare image content
                image_content = await self.get_image_content(submission_url)

                # Prepare messages
                messages = [
                    SystemMessage(content=enhanced_system_prompt),
                    HumanMessage(content=[text_content, image_content])
                ]

                print("\nSending request to LLM...")
                
                # Generate feedback
                response = await self.llm.agenerate([messages])
                raw_text = response.generations[0][0].text.strip()
                print("\nRaw LLM Response:")
                print(raw_text)

                try:
                    # Clean up the response text
                    cleaned_text = self.clean_json_response(raw_text)
                    print("\nCleaned Response Text:")
                    print(cleaned_text)
                    
                    feedback = json.loads(cleaned_text)
                    print("\nSuccessfully parsed JSON response")
                    
                    # Verify that all expected fields are present
                    for field in expected_format:
                        if field not in feedback:
                            if isinstance(expected_format[field], list):
                                feedback[field] = ["No information provided"]
                            else:
                                feedback[field] = f"No information provided for {field}"
                    
                except json.JSONDecodeError as e:
                    print(f"\nJSON Parse Error: {str(e)}")
                    print("Using fallback feedback format")
                    
                    # Create a fallback response matching the expected format
                    feedback = {}
                    for field in expected_format:
                        if isinstance(expected_format[field], list):
                            feedback[field] = ["Unable to generate proper feedback due to processing error"]
                        else:
                            feedback[field] = "Unable to generate proper feedback due to processing error"
                    
                    feedback["error"] = f"JSON parsing error: {str(e)}"
            else:
                print("\nInvalid submission detected - returning error feedback")
                # Create an error feedback matching the expected format
                feedback = {}
                
                # Set the special error message for overall_feedback
                feedback["overall_feedback"] = "Something went wrong—It looks like there's an issue from our end or your submission is incorrect! I am not able to provide feedback for your submission."
                
                # Fill in other required fields
                for field in expected_format:
                    if field != "overall_feedback":  # Skip overall_feedback as we've already set it
                        if isinstance(expected_format[field], list):
                            feedback[field] = ["Please ensure your submission matches the assignment requirements"]
                        else:
                            feedback[field] = "Please ensure your submission matches the assignment requirements"
                
                # Add detected type information
                feedback["detected_type"] = validation_result.get('detected_type', 'unknown')
                feedback["reason"] = validation_result.get('reason', 'Unknown issue with submission')

            print("\n=== Feedback Generation Completed Successfully ===")
            return feedback

        except Exception as e:
            error_msg = f"Error generating feedback for submission {submission_id}: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Feedback Generation Error")
            raise

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
            
            # Include any additional fields not in the standard format
            standard_fields = ["overall_feedback", "strengths", "areas_for_improvement", 
                              "learning_objectives_feedback", "grade_recommendation", 
                              "encouragement", "detected_type", "error"]
            
            for key, value in feedback.items():
                if key not in standard_fields:
                    formatted.append(f"\n{key.replace('_', ' ').title()}:")
                    if isinstance(value, list):
                        for item in value:
                            formatted.append(f"- {item}")
                    else:
                        formatted.append(str(value))
            
            return "\n".join(formatted)
            
        except Exception as e:
            error_msg = f"Error formatting feedback: {str(e)}"
            print(f"\nError: {error_msg}")
            return "Error formatting feedback"

    def get_current_config(self) -> Dict:
        """Get current LLM configuration"""
        if not self.llm:
            return {"status": "not_configured"}
            
        return {
            "provider": self.llm.__class__.__name__,
            "model": self.llm.model_name,
            "temperature": self.llm.temperature,
            "max_tokens": self.llm.max_tokens
        }
