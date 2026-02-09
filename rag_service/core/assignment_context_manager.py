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

    async def get_assignment_context(self, assignment_id: str, student_id: str) -> Dict:
        """Get assignment context from cache or API"""
        try:
            print(f"\n=== Getting Assignment Context for: {assignment_id} ===")

            context = None
            
            # Check cache if enabled
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
                    cached_context = frappe.get_doc("Assignment Context", cached_context[0].name).as_dict()
                    # remove fields where value is a datatime object
                    for key in list(cached_context.keys()):
                        if isinstance(cached_context[key], datetime):
                            cached_context.pop(key, None)
                    context = {"assignment": cached_context,}

            if not context:
                # If not in cache or caching disabled, fetch from API
                print("Fetching context from API...")
                context = await self._fetch_assignment_from_api(assignment_id)

                # Save to cache if enabled
                if self.settings.enable_caching:
                    print("Saving to cache...")
                    await self._save_to_cache(assignment_id, context)
                
                if context["assignment"]["rubrics"] is None:
                    print("Rubrics not found in assignment context.")
                    raise Exception("Rubrics missing in assignment context")
        
            if student_id is None:
                raise Exception("Student ID is required to fetch student context")
            
            student_details = await self._fetch_student_from_api(student_id)
            # student_details = {
            #                 "student_id":"ST0001",    
            #                 "grade":"6",
            #                 "level":"2",
            #                 "language": "Hindi"
            #             }
            context["student"] = {**student_details}

            # print("Assignment context",context)
            return context

        except Exception as e:
            error_msg = f"Error getting assignment context: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Assignment Context Error")
            raise

    async def _fetch_assignment_from_api(self, assignment_id: str) -> Dict:
        """Fetch assignment context from TAP LMS API"""
        try:
            # Construct API URL properly
            api_url = f"{self.settings.base_url.rstrip('/')}/{self.settings.assignment_context_endpoint.lstrip('/')}"
            
            payload = {
                "assignment_id": assignment_id
            }
            response = requests.post(
                api_url,
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
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

    async def _fetch_student_from_api(self, student_id: str) -> Dict:
        """Fetch student details from TAP LMS API"""
        try:
            # Construct API URL properly
            api_url = f"{self.settings.base_url.rstrip('/')}/{self.settings.student_context_endpoint.lstrip('/')}"
            
            payload = {
                "student_id": student_id
            }
            response = requests.post(
                api_url,
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code != 200:
                error_msg = f"API request failed with status {response.status_code}: {response.text}"
                print(f"Error: {error_msg}")
                raise Exception(error_msg)
            
            data = response.json()
            if "message" not in data:
                print(f"Error: Invalid API response format: {data}")
                raise Exception("Invalid API response format")
            
            print("Student API request successful")
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
                    "version": (doc.version or 0) + 1,
                    "rubrics": json.dumps(assignment.get("rubrics", {}))
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
                    "version": 1,
                    "rubrics": json.dumps(assignment.get("rubrics", {}))
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

    async def refresh_cache(self, assignment_id: str) -> None:
        """Manually refresh cache for an assignment"""
        try:
            print(f"\n=== Refreshing Cache for Assignment: {assignment_id} ===")
            
            # Force fetch from API
            context = await self._fetch_assignment_from_api(assignment_id)
            
            # Save to cache
            await self._save_to_cache(assignment_id, context)
            
            print("Cache refreshed successfully")
            
        except Exception as e:
            error_msg = f"Error refreshing cache: {str(e)}"
            print(f"\nError: {error_msg}")
            raise Exception(error_msg)
