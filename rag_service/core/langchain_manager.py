# rag_service/rag_service/core/langchain_manager.py

import frappe
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage
import json
from typing import Dict, List, Optional
import httpx

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

            # Format learning objectives
            learning_objectives = self.format_objectives(assignment_context["learning_objectives"])
            print("\nLearning objectives formatted")

            # Prepare text content
            text_content = {
                "type": "text",
                "text": template.user_prompt.format(
                    assignment_description=assignment_context["assignment"]["description"],
                    learning_objectives=learning_objectives
                )
            }
            print("\nText content prepared")

            # Prepare image content
            image_content = await self.get_image_content(submission_url)
            print("\nImage content prepared")

            # Prepare messages
            messages = [
                SystemMessage(content=template.system_prompt),
                HumanMessage(content=[text_content, image_content])
            ]
            
            print("\nSending request to OpenAI...")
            
            # Generate feedback
            response = await self.llm.agenerate([messages])
            print("\nResponse received from OpenAI")

            # Get raw text and clean it
            raw_text = response.generations[0][0].text
            print("\nRaw LLM Response:")
            print(raw_text)

            # Clean up the response text
            cleaned_text = raw_text
            if "```json" in cleaned_text:
                cleaned_text = cleaned_text.split("```json")[1].split("```")[0].strip()
            
            # Parse response
            try:
                feedback = json.loads(cleaned_text)
                print("\nSuccessfully parsed JSON response")
            except json.JSONDecodeError as e:
                print(f"\nJSON Parse Error: {str(e)}")
                print("Failed to parse text:")
                print(cleaned_text)
                raise ValueError(f"Invalid JSON response: {str(e)}")

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
            frappe.log_error(error_msg, "Feedback Formatting Error")
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
