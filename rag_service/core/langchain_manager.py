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
            # Remove JSON code blocks if present
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].strip()
                
            # Try to extract JSON if response starts with explanation
            if not response.strip().startswith('{'):
                potential_json = response.split('{', 1)
                if len(potential_json) > 1:
                    response = '{' + potential_json[1]
                    
            return response.strip()
        except Exception:
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
            except json.JSONDecodeError:
                return {
                    "is_valid": False,
                    "reason": "Failed to validate image format",
                    "detected_type": "unknown"
                }

        except Exception as e:
            error_msg = f"Image validation failed: {str(e)}"
            print(f"\nError: {error_msg}")
            return {
                "is_valid": False,
                "reason": error_msg,
                "detected_type": "error"
            }

    def format_objectives(self, objectives: List[Dict]) -> str:
        """Format learning objectives for prompt"""
        return "\n".join([
            f"- {obj['description']}" 
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

    async def generate_feedback(self, assignment_context: Dict, submission_url: str, submission_id: str) -> Dict:
        """Generate feedback using LangChain and GPT-4V"""
        try:
            print("\n=== Starting Feedback Generation ===")
            
            # Get prompt template
            template = self.get_prompt_template(assignment_context["assignment"]["type"])
            print("\nTemplate loaded successfully")

            # Validate image first
            validation_result = await self.validate_submission_image(
                submission_url, 
                assignment_context["assignment"]["type"]
            )

            # Process based on validation result
            if validation_result.get("is_valid", False):
                print("\nValid submission detected - generating feedback")
                
                # Format learning objectives
                learning_objectives = self.format_objectives(assignment_context["learning_objectives"])
                
                # Enhanced system prompt to enforce JSON response
                enhanced_system_prompt = f"""
                {template.system_prompt}
                
                IMPORTANT: You must ALWAYS respond with a valid JSON object containing exactly these fields:
                {{
                    "overall_feedback": "detailed feedback about the artwork",
                    "strengths": ["list", "of", "strengths"],
                    "areas_for_improvement": ["list", "of", "improvements"],
                    "learning_objectives_feedback": ["feedback", "for", "each", "objective"],
                    "grade_recommendation": "numerical grade",
                    "encouragement": "encouraging message"
                }}

                If you cannot analyze the image for any reason, provide feedback indicating the issue while maintaining this exact JSON format.
                Do not include any additional text or explanations outside the JSON object.
                """

                # Prepare text content
                text_content = {
                    "type": "text",
                    "text": template.user_prompt.format(
                        assignment_description=assignment_context["assignment"]["description"],
                        learning_objectives=learning_objectives
                    )
                }

                # Prepare image content
                image_content = await self.get_image_content(submission_url)

                # Prepare messages
                messages = [
                    SystemMessage(content=enhanced_system_prompt),
                    HumanMessage(content=[text_content, image_content])
                ]

                print("\nSending request to OpenAI...")
                
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
                    
                except json.JSONDecodeError as e:
                    print(f"\nJSON Parse Error: {str(e)}")
                    print("Using fallback feedback format")
                    
                    # Fallback JSON response when LLM doesn't provide valid JSON
                    feedback = {
                        "overall_feedback": "Something went wrong—It looks like there's an issue from our end or your submission is incorrect! I am not able to provide feedback for your submission.",
                        "strengths": [
                            "Submission attempt was made",
                            "Student engaged with the assignment process"
                        ],
                        "areas_for_improvement": [
                            "Please ensure the submitted work matches the assignment requirements",
                            "Consider resubmitting with a clearer or more appropriate image",
                            "Review the assignment instructions carefully"
                        ],
                        "learning_objectives_feedback": [
                            "Unable to evaluate learning objectives due to issues with the submission"
                        ],
                        "grade_recommendation": "0",
                        "encouragement": "We encourage you to review the assignment requirements and submit work that aligns with the expected format and content. Feel free to reach out to your instructor if you need clarification."
                    }
            else:
                print("\nInvalid submission detected - returning error feedback")
                feedback = {
                    "overall_feedback": "Something went wrong—It looks like there's an issue from our end or your submission is incorrect! I am not able to provide feedback for your submission.",
                    "strengths": [
                        "Submission attempt was made",
                        "Student engaged with the assignment process"
                    ],
                    "areas_for_improvement": [
                        f"Current submission appears to be: {validation_result.get('detected_type', 'unknown')}",
                        "Please ensure the submitted work matches the assignment requirements",
                        "Review the assignment instructions carefully before resubmitting"
                    ],
                    "learning_objectives_feedback": [
                        "Could not evaluate learning objectives due to invalid submission"
                    ],
                    "grade_recommendation": "0",
                    "encouragement": "We look forward to reviewing your actual artwork submission. Please make sure to submit work that matches the assignment requirements. Don't hesitate to ask for clarification if needed."
                }

            # Validate feedback structure
            required_fields = [
                "overall_feedback",
                "strengths",
                "areas_for_improvement",
                "learning_objectives_feedback",
                "grade_recommendation",
                "encouragement"
            ]
            
            missing_fields = [field for field in required_fields if field not in feedback]
            if missing_fields:
                raise ValueError(f"Missing required fields in feedback: {missing_fields}")

            print("\n=== Feedback Generation Completed Successfully ===")
            return feedback

        except Exception as e:
            error_msg = f"Error generating feedback for submission {submission_id}: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(message=error_msg, title="Feedback Generation Error")
            raise

    def get_prompt_template(self, assignment_type: str) -> Dict:
        """Get active prompt template for assignment type"""
        try:
            templates = frappe.get_list(
                "Prompt Template",
                filters={
                    "assignment_type": assignment_type,
                    "is_active": 1
                },
                order_by="version desc",
                limit=1
            )
            
            if not templates:
                raise Exception(f"No active prompt template found for {assignment_type}")
                
            template = frappe.get_doc("Prompt Template", templates[0].name)
            print(f"\nUsing template: {template.template_name}")
            return template
            
        except Exception as e:
            error_msg = f"Prompt Template Error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Prompt Template Error")
            raise

    @staticmethod
    def format_feedback_for_display(feedback: Dict) -> str:
        """Format feedback for human-readable display"""
        try:
            formatted = []
            
            formatted.append("Overall Feedback:")
            formatted.append(feedback["overall_feedback"])
            
            formatted.append("\nStrengths:")
            for strength in feedback["strengths"]:
                formatted.append(f"- {strength}")
                
            formatted.append("\nAreas for Improvement:")
            for area in feedback["areas_for_improvement"]:
                formatted.append(f"- {area}")
                
            formatted.append("\nLearning Objectives Feedback:")
            for obj in feedback["learning_objectives_feedback"]:
                formatted.append(f"- {obj}")
                
            formatted.append(f"\nGrade Recommendation: {feedback['grade_recommendation']}")
            formatted.append(f"\nEncouragement: {feedback['encouragement']}")
            
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
