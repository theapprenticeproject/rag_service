# rag_service/rag_service/core/assignment_context_manager.py

import frappe
import json
import requests
from datetime import timedelta
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
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    async def get_assignment_context(self, assignment_id: str) -> Dict:
        """Get assignment context from cache or API"""
        try:
            print(f"\n=== Getting Assignment Context for: {assignment_id} ===")

            context = None
            
            # Check cache if enabled
            if self.settings.enable_caching:
                cache_duration = self.settings.cache_duration_days or 1
                cache_updated_after = now_datetime() - timedelta(days=cache_duration)
                cached_context = frappe.get_list(
                    "Assignment Context",
                    filters={
                        "name": assignment_id,
                        "update_time": [">", cache_updated_after]
                    },
                    fields=["name", "context"],
                    limit=1
                )
                
                if cached_context:
                    print("Found cached context")
                    raw_context = cached_context[0].get("context")
                    if raw_context:
                        context = json.loads(raw_context)

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
        
            # if student_id is None:
            #     raise Exception("Student ID is required to fetch student context")
            
            # student_details = await self._fetch_student_from_api(student_id)
            # # student_details = {
            # #                 "student_id":"ST0001",    
            # #                 "grade":"6",
            # #                 "level":"2",
            # #                 "language": "Hindi"
            # #             }
            # context["student"] = {**student_details}

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
            
            data = response.json()["message"]
            print(data)
            # data = {'assignment': {'name': 'Pop Art', 'program_name': 'Summer Program 2026', 'description': 'Students will create a Pop Art–style artwork inspired by Andy Warhol by drawing one simple object four times on their paper and coloring each version using different pairs of complementary colors. They will experiment with bold outlines, bright contrasts, and simple background patterns to make their artwork look vibrant and balanced.', 'assignment_type': 'Written', 'activity_type': 'Regular', 'course_vertical': 'Arts', 'difficulty_tier': 'Remedial', 'submission_guidelines': '', 'submission_rules': [{'submission_title': 'Emoji', 'allowed_submission_types': ['emoji'], 'guided_text': 'Send a 👍 if you enjoyed the activity, or 👎 if you did not enjoy it', 'unguided_text': 'Please send any emoji of your choice!', 'valid_criteria': '👍 or 👎', 'invalid_criteria': 'Any emoji other than 👍 or 👎'}, {'submission_title': 'Type a word or send a voice note', 'allowed_submission_types': ['text', 'audio'], 'guided_text': 'Send a text or voice note saying "Creative" if you liked it, or "Boring" if you didn’t.', 'unguided_text': 'What do you think of the Pop Art activity? Share in one word or send a voice note', 'valid_criteria': 'Creative, Boring', 'invalid_criteria': 'Any word other than creative, boring'}, {'submission_title': 'Taking a picture of anything around you', 'allowed_submission_types': ['image'], 'guided_text': 'Share a picture of anything in red color', 'unguided_text': 'Share a picture of anything around you', 'valid_criteria': 'Picture of anything in red color', 'invalid_criteria': 'Anything other which is not red in color'}, {'submission_title': 'Send a voice/text summary Related to Artefact', 'allowed_submission_types': ['text', 'audio'], 'guided_text': 'Type or send a voice note and tell us one complementary color pair', 'unguided_text': '', 'valid_criteria': 'red–green, blue–orange, yellow–purple', 'invalid_criteria': 'wrong pair, single color, sentences'}, {'submission_title': 'Take a picture or video of the created artefact Related Artefact', 'allowed_submission_types': ['image', 'video'], 'guided_text': 'Don’t forget to check if:\n\nYou drew the same object 4 times\nYou used 2 complementary color pairs\nYou added bold outlines\nYour coloring is neat and filled', 'unguided_text': '', 'valid_criteria': '- Clear artwork using complementary colors\n- Neat coloring and bold outlines\n- Complete artwork visible\n\nAll criteria needs to be fulfilled', 'invalid_criteria': '- Missing complementary colors\n- Messy or uneven coloring\n- Incomplete artwork\n- Unclear/cropped image'}], 'reference_images': [], 'max_score': None, 'rubrics': {}}, 'learning_objectives': []}
            # print("#############")
            # print(data)
            # print("#############")
            return data
            
        except requests.RequestException as e:
            error_msg = f"API request failed: {str(e)}"
            print(f"\nError: {error_msg}")
            raise Exception(error_msg)

    # async def _fetch_student_from_api(self, student_id: str) -> Dict:
    #     """Fetch student details from TAP LMS API"""
    #     try:
    #         # Construct API URL properly
    #         api_url = f"{self.settings.base_url.rstrip('/')}/{self.settings.student_context_endpoint.lstrip('/')}"
            
    #         payload = {
    #             "student_id": student_id
    #         }
    #         response = requests.post(
    #             api_url,
    #             headers=self.headers,
    #             json=payload,
    #             timeout=30
    #         )
            
    #         if response.status_code != 200:
    #             error_msg = f"API request failed with status {response.status_code}: {response.text}"
    #             print(f"Error: {error_msg}")
    #             raise Exception(error_msg)
            
    #         data = response.json()
    #         # data = {'student_id': 'ST00000182', 'grade': '5', 'level': 'L1', 'language': 'Hindi'}
            
    #         print("Student API request successful")
    #         return data
            
    #     except requests.RequestException as e:
    #         error_msg = f"API request failed: {str(e)}"
    #         print(f"\nError: {error_msg}")
    #         raise Exception(error_msg)

    async def _save_to_cache(self, assignment_id: str, context: Dict) -> None:
        """Save assignment context to cache"""
        try:
            update_time = now_datetime()
            payload = self._json_dumps(context)
            existing = frappe.db.exists("Assignment Context", assignment_id)
            
            if existing:
                # Update existing
                doc = frappe.get_doc("Assignment Context", existing)
                doc.update({
                    "assignment_id": assignment_id,
                    "update_time": update_time,
                    "context": payload
                })
                doc.save()
                print(f"Updated existing cache for assignment {assignment_id}")
            else:
                # Create new
                doc = frappe.get_doc({
                    "doctype": "Assignment Context",
                    "name": assignment_id,
                    "assignment_id": assignment_id,
                    "update_time": update_time,
                    "context": payload
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
