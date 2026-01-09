# rag_service/rag_service/core/assignment_context_manager.py

import frappe
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional
from frappe.utils import now_datetime

class AssignmentContextManager:
    def __init__(self):
        self.settings = frappe.get_single("RAG Settings")
        
        # Construct headers with proper authentication
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"token {self.settings.api_key}:{self.settings.get_password('api_secret')}"
        }
        print("\nInitialized AssignmentContextManager")
        print(f"Using API Endpoint: {self.settings.base_url.rstrip('/')}/{self.settings.assignment_context_endpoint.lstrip('/')}")

    async def get_assignment_context(self, assignment_id: str) -> Dict:
        """Get assignment context from cache or API"""
        try:
            print(f"\n=== Getting Assignment Context for: {assignment_id} ===")
            
            # 1. Check cache if enabled
            if self.settings.enable_caching:
                cached_context = frappe.get_list(
                    "Assignment Context",
                    filters={
                        "assignment_id": assignment_id,
                        "cache_valid_till": [">", now_datetime()]
                    },
                    limit=1
                )
                
                if cached_context:
                    print("Found cached context")
                    return await self._format_cached_context(cached_context[0].name)
            
            # 2. If not in cache or caching disabled, fetch from API
            print("Fetching context from API...")
            context = await self._fetch_from_api(assignment_id)
            
            # 3. Save to cache if enabled
            if self.settings.enable_caching:
                print("Saving to cache...")
                await self._save_to_cache(assignment_id, context)
            
            # 4. Format and return
            return self._format_context_for_llm(context)
            
        except Exception as e:
            error_msg = f"Error getting assignment context: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Assignment Context Error")
            raise

    async def _fetch_from_api(self, assignment_id: str) -> Dict:
        """Fetch assignment context from TAP LMS API"""
        try:
            # Construct API URL properly
            api_url = f"{self.settings.base_url.rstrip('/')}/{self.settings.assignment_context_endpoint.lstrip('/')}"
            print(f"\nMaking API request to: {api_url}")
            
            payload = {
                "assignment_id": assignment_id
            }
            
            print("\nRequest Details:")
            print(f"Headers: {json.dumps({k: v if k != 'Authorization' else '[REDACTED]' for k, v in self.headers.items()}, indent=2)}")
            print(f"Payload: {json.dumps(payload, indent=2)}")
            
            response = requests.post(
                api_url,
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            print(f"\nResponse Status: {response.status_code}")
            
            if response.status_code != 200:
                error_msg = f"API request failed with status {response.status_code}: {response.text}"
                print(f"Error: {error_msg}")
                raise Exception(error_msg)
            
            data = response.json()
            if "message" not in data:
                raise Exception("Invalid API response format")
            
            print("API request successful")
            return data["message"]
            
        except requests.RequestException as e:
            error_msg = f"API request failed: {str(e)}"
            print(f"\nError: {error_msg}")
            raise Exception(error_msg)

    async def _save_to_cache(self, assignment_id: str, context: Dict) -> None:
        """Save assignment context to cache"""
        try:
            # Calculate cache expiry
            cache_duration = self.settings.cache_duration_days or 1
            cache_valid_till = now_datetime() + timedelta(days=cache_duration)
            
            # Extract assignment data
            assignment = context["assignment"]
            learning_objectives = context.get("learning_objectives", [])
            
            # Parse assignment type properly
            assignment_type = assignment.get("type", "Practical")
            
            # If type is empty or invalid, default to Practical
            valid_types = ["Written", "Practical", "Performance", "Collaborative"]
            if not assignment_type or assignment_type not in valid_types:
                assignment_type = "Practical"
                print(f"Invalid assignment type '{assignment.get('type')}', defaulting to 'Practical'")
            
            # Prepare learning objectives JSON
            formatted_objectives = []
            if learning_objectives:
                formatted_objectives = [
                    {
                        "objective_id": obj.get("objective", "Unknown"),
                        "description": obj.get("description", "").strip()
                    }
                    for obj in learning_objectives
                ]
            
            # Check for existing context
            existing = frappe.get_list(
                "Assignment Context",
                filters={"assignment_id": assignment_id},
                limit=1
            )
            
            # Determine course vertical
            course_vertical = "General"
            if "subject" in assignment and assignment["subject"]:
                subject_parts = assignment["subject"].split("-")
                if len(subject_parts) > 1:
                    course_vertical = subject_parts[-1].strip()
            
            if existing:
                # Update existing
                doc = frappe.get_doc("Assignment Context", existing[0].name)
                doc.update({
                    "assignment_name": assignment.get("name", ""),
                    "course_vertical": course_vertical,
                    "assignment_type": assignment_type,
                    "reference_image": assignment.get("reference_image", ""),
                    "description": assignment.get("description", ""),
                    "learning_objectives": json.dumps(formatted_objectives),
                    "max_score": assignment.get("max_score", "100"),
                    "last_updated": now_datetime(),
                    "cache_valid_till": cache_valid_till,
                    "last_sync_status": "Success",
                    "version": (doc.version or 0) + 1
                })
                doc.save()
                print(f"Updated existing cache for assignment {assignment_id}")
            else:
                # Create new
                doc = frappe.get_doc({
                    "doctype": "Assignment Context",
                    "assignment_id": assignment_id,
                    "assignment_name": assignment.get("name", ""),
                    "course_vertical": course_vertical,
                    "assignment_type": assignment_type,
                    "reference_image": assignment.get("reference_image", ""),
                    "description": assignment.get("description", ""),
                    "learning_objectives": json.dumps(formatted_objectives),
                    "max_score": assignment.get("max_score", "100"),
                    "difficulty_level": "Medium",  # Default value
                    "last_updated": now_datetime(),
                    "cache_valid_till": cache_valid_till,
                    "last_sync_status": "Success",
                    "version": 1
                })
                doc.insert()
                print(f"Created new cache for assignment {assignment_id}")
            
            frappe.db.commit()
            print(f"Context cached successfully for {assignment_id}")
            
        except Exception as e:
            error_msg = f"Error saving to cache: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.db.rollback()  # Rollback on error
            raise Exception(error_msg)

    async def _format_cached_context(self, context_name: str) -> Dict:
        """Format cached context for LLM"""
        try:
            context = frappe.get_doc("Assignment Context", context_name)
            
            # Parse learning objectives safely
            learning_objectives = []
            try:
                if context.learning_objectives:
                    learning_objectives = json.loads(context.learning_objectives)
            except (json.JSONDecodeError, TypeError) as e:
                print(f"Error parsing learning objectives: {str(e)}")
                # Create an empty default if parsing fails
                learning_objectives = []
            
            return {
                "assignment": {
                    "id": context.assignment_id,
                    "name": context.assignment_name,
                    "type": context.assignment_type,
                    "description": context.description,
                    "max_score": context.max_score,
                    "reference_image": context.reference_image
                },
                "learning_objectives": learning_objectives,
                "course_vertical": context.course_vertical,
                "difficulty_level": context.difficulty_level
            }
            
        except Exception as e:
            error_msg = f"Error formatting cached context: {str(e)}"
            print(f"\nError: {error_msg}")
            raise Exception(error_msg)

    def _format_context_for_llm(self, api_context: Dict) -> Dict:
        """Format API context for LLM"""
        try:
            assignment = api_context["assignment"]
            
            # Determine course vertical
            course_vertical = "General"
            if "subject" in assignment and assignment["subject"]:
                subject_parts = assignment["subject"].split("-")
                if len(subject_parts) > 1:
                    course_vertical = subject_parts[-1].strip()
                    
            # Parse assignment type correctly
            assignment_type = assignment.get("type", "Practical")
            valid_types = ["Written", "Practical", "Performance", "Collaborative"]
            if not assignment_type or assignment_type not in valid_types:
                assignment_type = "Practical"
            
            # Format learning objectives
            learning_objectives = []
            if "learning_objectives" in api_context and api_context["learning_objectives"]:
                learning_objectives = [
                    {
                        "objective_id": obj.get("objective", "Unknown"),
                        "description": obj.get("description", "").strip()
                    }
                    for obj in api_context["learning_objectives"]
                ]
            
            return {
                "assignment": {
                    "id": assignment.get("name", ""),  # Using name as ID
                    "name": assignment.get("name", ""),
                    "type": assignment_type,
                    "description": assignment.get("description", ""),
                    "max_score": assignment.get("max_score", "100"),
                    "reference_image": assignment.get("reference_image", "")
                },
                "learning_objectives": learning_objectives,
                "course_vertical": course_vertical,
                "difficulty_level": "Medium"  # Default value
            }
            
        except Exception as e:
            error_msg = f"Error formatting API context: {str(e)}"
            print(f"\nError: {error_msg}")
            raise Exception(error_msg)

    async def refresh_cache(self, assignment_id: str) -> None:
        """Manually refresh cache for an assignment"""
        try:
            print(f"\n=== Refreshing Cache for Assignment: {assignment_id} ===")
            
            # Force fetch from API
            context = await self._fetch_from_api(assignment_id)
            
            # Save to cache
            await self._save_to_cache(assignment_id, context)
            
            print("Cache refreshed successfully")
            
        except Exception as e:
            error_msg = f"Error refreshing cache: {str(e)}"
            print(f"\nError: {error_msg}")
            raise Exception(error_msg)

    # def verify_settings(self) -> Dict:
    #     """Verify RAG Settings configuration"""
    #     try:
    #         results = {
    #             "base_url": bool(self.settings.base_url),
    #             "api_key": bool(self.settings.api_key),
    #             "api_secret": bool(self.settings.get_password('api_secret')),
    #             "endpoints": bool(self.settings.assignment_context_endpoint),
    #             "cache_config": bool(self.settings.cache_duration_days is not None)
    #         }
            
    #         missing = [k for k, v in results.items() if not v]
            
    #         return {
    #             "status": "Valid" if not missing else "Invalid",
    #             "missing_settings": missing,
    #             "cache_enabled": self.settings.enable_caching,
    #             "cache_duration": self.settings.cache_duration_days
    #         }
            
    #     except Exception as e:
    #         return {
    #             "status": "Error",
    #             "error": str(e)
    #         }
