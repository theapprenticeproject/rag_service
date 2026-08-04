# rag_service/rag_service/core/assignment_context_manager.py

import frappe
import json
import requests
from datetime import datetime, timedelta
from typing import Any, Dict
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

    @staticmethod
    def _safe_text(value: Any, default: str = "") -> str:
        """Return a stripped string while treating explicit None as missing."""
        if value is None:
            return default
        return str(value).strip()

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    def _format_learning_objectives(self, learning_objectives: Any) -> list:
        if not learning_objectives:
            return []

        formatted_objectives = []
        for obj in learning_objectives:
            if not isinstance(obj, dict):
                continue

            formatted_objectives.append({
                "objective_id": self._safe_text(obj.get("objective"), "Unknown"),
                "description": self._safe_text(obj.get("description"))
            })

        return formatted_objectives

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
            raise Exception(error_msg)

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
            print(data)
            # data = {'assignment': {'name': 'Pop Art', 'program_name': 'Summer Program 2026', 'description': 'Students will create a Pop Art–style artwork inspired by Andy Warhol by drawing one simple object four times on their paper and coloring each version using different pairs of complementary colors. They will experiment with bold outlines, bright contrasts, and simple background patterns to make their artwork look vibrant and balanced.', 'assignment_type': 'Written', 'activity_type': 'Regular', 'course_vertical': 'Arts', 'difficulty_tier': 'Remedial', 'submission_guidelines': '', 'submission_rules': [{'submission_title': 'Emoji', 'allowed_submission_types': ['emoji'], 'guided_text': 'Send a 👍 if you enjoyed the activity, or 👎 if you did not enjoy it', 'unguided_text': 'Please send any emoji of your choice!', 'valid_criteria': '👍 or 👎', 'invalid_criteria': 'Any emoji other than 👍 or 👎'}, {'submission_title': 'Type a word or send a voice note', 'allowed_submission_types': ['text', 'audio'], 'guided_text': 'Send a text or voice note saying "Creative" if you liked it, or "Boring" if you didn’t.', 'unguided_text': 'What do you think of the Pop Art activity? Share in one word or send a voice note', 'valid_criteria': 'Creative, Boring', 'invalid_criteria': 'Any word other than creative, boring'}, {'submission_title': 'Taking a picture of anything around you', 'allowed_submission_types': ['image'], 'guided_text': 'Share a picture of anything in red color', 'unguided_text': 'Share a picture of anything around you', 'valid_criteria': 'Picture of anything in red color', 'invalid_criteria': 'Anything other which is not red in color'}, {'submission_title': 'Send a voice/text summary Related to Artefact', 'allowed_submission_types': ['text', 'audio'], 'guided_text': 'Type or send a voice note and tell us one complementary color pair', 'unguided_text': '', 'valid_criteria': 'red–green, blue–orange, yellow–purple', 'invalid_criteria': 'wrong pair, single color, sentences'}, {'submission_title': 'Take a picture or video of the created artefact Related Artefact', 'allowed_submission_types': ['image', 'video'], 'guided_text': 'Don’t forget to check if:\n\nYou drew the same object 4 times\nYou used 2 complementary color pairs\nYou added bold outlines\nYour coloring is neat and filled', 'unguided_text': '', 'valid_criteria': '- Clear artwork using complementary colors\n- Neat coloring and bold outlines\n- Complete artwork visible\n\nAll criteria needs to be fulfilled', 'invalid_criteria': '- Missing complementary colors\n- Messy or uneven coloring\n- Incomplete artwork\n- Unclear/cropped image'}], 'reference_images': [], 'max_score': None, 'rubrics': {}}, 'learning_objectives': []}
            # print("#############")
            # print(data)
            # print("#############")
            print("API request successful")
            return data
            
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
            # data = {'student_id': 'ST00000182', 'grade': '5', 'level': 'L1', 'language': 'Hindi'}
            
            print("Student API request successful")
            return data
            
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
            assignment_type = self._safe_text(assignment.get("assignment_type"), "Practical")
            course_vertical = self._safe_text(assignment.get("course_vertical"), "General")
            activity_type = self._safe_text(assignment.get("activity_type"))
            program_name = self._safe_text(assignment.get("program_name"))
            difficulty_tier = self._safe_text(assignment.get("difficulty_tier"))
            submission_guidelines = self._safe_text(assignment.get("submission_guidelines"))
            submission_rules = self._json_dumps(assignment.get("submission_rules", []))
            print(f"Parsed assignment type: {assignment_type}, course vertical: {course_vertical}, activity type: {activity_type}, program name: {program_name}, difficulty tier: {difficulty_tier}")
            
            # If type is empty or invalid, default to Practical

            
            # Prepare learning objectives JSON
            formatted_objectives = self._format_learning_objectives(learning_objectives)
            
            # Check for existing context
            existing = frappe.get_list(
                "Assignment Context",
                filters={"assignment_id": assignment_id},
                limit=1
            )
            
            if existing:
                # Update existing
                doc = frappe.get_doc("Assignment Context", existing[0].name)
                doc.update({
                    "assignment_name": self._safe_text(assignment.get("name")),
                    "course_vertical": course_vertical,
                    "assignment_type": assignment_type,
                    "activity_type": activity_type,
                    "reference_image": self._safe_text(assignment.get("reference_image")),
                    "description": self._safe_text(assignment.get("description")),
                    "learning_objectives": self._json_dumps(formatted_objectives),
                    "max_score": self._safe_text(assignment.get("max_score"), "100"),
                    "program_name": program_name,
                    "difficulty_tier": difficulty_tier,
                    "submission_guidelines": submission_guidelines,
                    "submission_rules": submission_rules,
                    "last_updated": now_datetime(),
                    "cache_valid_till": cache_valid_till,
                    "last_sync_status": "Success",
                    "version": (doc.version or 0) + 1,
                    "rubrics": self._json_dumps(assignment.get("rubrics", {}))
                })
                doc.save()
                print(f"Updated existing cache for assignment {assignment_id}")
            else:
                # Create new
                doc = frappe.get_doc({
                    "doctype": "Assignment Context",
                    "assignment_id": assignment_id,
                    "assignment_name": self._safe_text(assignment.get("name")),
                    "course_vertical": course_vertical,
                    "assignment_type": assignment_type,
                    "activity_type": activity_type,
                    "reference_image": self._safe_text(assignment.get("reference_image")),
                    "description": self._safe_text(assignment.get("description")),
                    "learning_objectives": self._json_dumps(formatted_objectives),
                    "max_score": self._safe_text(assignment.get("max_score"), "100"),
                    "difficulty_level": "Medium",  # Default value
                    "program_name": program_name,
                    "difficulty_tier": difficulty_tier,
                    "submission_guidelines": submission_guidelines,
                    "submission_rules": submission_rules,
                    "last_updated": now_datetime(),
                    "cache_valid_till": cache_valid_till,
                    "last_sync_status": "Success",
                    "version": 1,
                    "rubrics": self._json_dumps(assignment.get("rubrics", {}))
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
