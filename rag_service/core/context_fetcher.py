# rag_service/rag_service/core/context_fetcher.py

import frappe
import json
import httpx
from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import urljoin

class AssignmentContextFetcher:
    def __init__(self):
        self.settings = frappe.get_single("RAG Settings")
        self.api_url = urljoin(
            self.settings.base_url,
            self.settings.assignment_context_endpoint
        )
        self.cache_duration = timedelta(days=self.settings.cache_duration_days)
        self.enable_caching = self.settings.enable_caching
        self.max_retries = 3
        self.retry_delay = 1  # seconds
        
    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with authentication"""
        api_key = self.settings.api_key
        api_secret = self.settings.get_password('api_secret')
        
        return {
            "Authorization": f"token {api_key}:{api_secret}",
            "Content-Type": "application/json"
        }

    async def get_assignment_context(self, assignment_id: str) -> Dict:
        """
        Get assignment context - first check cache, then fetch from API if needed
        """
        try:
            # Check cache if enabled
            if self.enable_caching:
                cached_context = self._get_cached_context(assignment_id)
                if cached_context:
                    frappe.logger().debug(f"Cache hit for assignment {assignment_id}")
                    return cached_context
            
            # If not in cache or caching disabled, fetch from API
            context_data = await self._fetch_from_api(assignment_id)
            
            # Cache if enabled
            if self.enable_caching:
                self._cache_context(assignment_id, context_data)
                frappe.logger().debug(f"Cached context for assignment {assignment_id}")
            
            return context_data
            
        except Exception as e:
            frappe.log_error(
                message=f"Error fetching assignment context: {str(e)}",
                title="Assignment Context Error"
            )
            raise

    async def _fetch_from_api(self, assignment_id: str) -> Dict:
        """Fetch context from TAP LMS API with retries"""
        last_error = None
        headers = self._get_headers()
        
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self.api_url,
                        json={"assignment_id": assignment_id},
                        headers=headers,
                        timeout=30.0
                    )
                    
                    if response.status_code == 401:
                        raise ValueError("Authentication failed - check API key and secret")
                        
                    response.raise_for_status()
                    
                    context_data = response.json()
                    if not context_data.get("message"):
                        raise ValueError("Invalid response format from API")
                        
                    frappe.logger().debug(f"Successfully fetched context for assignment {assignment_id}")
                    return context_data["message"]
                    
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP error {e.response.status_code}: {str(e)}"
                if e.response.status_code in [401, 403, 404]:  # Don't retry auth or not found errors
                    break
                await self._wait_before_retry(attempt)
                
            except httpx.RequestError as e:
                last_error = f"Request error: {str(e)}"
                await self._wait_before_retry(attempt)
                
            except Exception as e:
                last_error = str(e)
                await self._wait_before_retry(attempt)
        
        frappe.log_error(
            message=f"Failed to fetch context after {self.max_retries} attempts: {last_error}",
            title="API Error"
        )
        raise Exception(f"Failed to fetch assignment context: {last_error}")

    async def _wait_before_retry(self, attempt: int):
        """Exponential backoff for retries"""
        import asyncio
        wait_time = self.retry_delay * (2 ** attempt)  # exponential backoff
        await asyncio.sleep(wait_time)

    def _get_cached_context(self, assignment_id: str) -> Optional[Dict]:
        """Check if we have a valid cached context"""
        try:
            cached = frappe.get_list(
                "Assignment Context",
                filters={
                    "assignment_id": assignment_id,
                    "cache_valid_till": [">", datetime.now()],
                    "last_sync_status": "Success"
                },
                order_by="version desc",
                limit=1
            )
            
            if not cached:
                return None
                
            context_doc = frappe.get_doc("Assignment Context", cached[0].name)
            return {
                "assignment": {
                    "name": context_doc.assignment_name,
                    "description": context_doc.description,
                    "type": context_doc.assignment_type,
                    "subject": context_doc.course_vertical,
                    "submission_guidelines": context_doc.submission_guidelines,
                    "reference_image": context_doc.reference_image,
                    "max_score": context_doc.max_score,
                },
                "learning_objectives": json.loads(context_doc.learning_objectives)
            }
            
        except Exception as e:
            frappe.log_error(
                message=f"Error retrieving cached context: {str(e)}", 
                title="Cache Retrieval Error"
            )
            return None

    def _cache_context(self, assignment_id: str, context_data: Dict) -> None:
        """Store context in cache"""
        try:
            assignment = context_data["assignment"]
            cache_valid_till = datetime.now() + self.cache_duration
            
            doc = frappe.get_doc({
                "doctype": "Assignment Context",
                "assignment_id": assignment_id,
                "assignment_name": assignment["name"],
                "course_vertical": assignment["subject"],
                "description": assignment["description"],
                "submission_guidelines": assignment["submission_guidelines"],
                "reference_image": assignment["reference_image"],
                "learning_objectives": json.dumps(context_data["learning_objectives"]),
                "max_score": assignment["max_score"],
                "last_updated": datetime.now(),
                "cache_valid_till": cache_valid_till,
                "last_sync_status": "Success",
                "version": 1
            })
            
            existing_docs = frappe.get_list(
                "Assignment Context",
                filters={"assignment_id": assignment_id}
            )
            
            if existing_docs:
                doc.name = existing_docs[0].name
                doc.version = frappe.get_value("Assignment Context", existing_docs[0].name, "version") + 1
                
            doc.save()
            frappe.db.commit()
            
        except Exception as e:
            frappe.log_error(
                message=f"Error caching context: {str(e)}", 
                title="Cache Error"
            )
            raise

    def invalidate_cache(self, assignment_id: str) -> None:
        """Manually invalidate cache for an assignment"""
        try:
            frappe.db.sql("""
                UPDATE `tabAssignment Context` 
                SET cache_valid_till = NOW()
                WHERE assignment_id = %s
            """, (assignment_id,))
            frappe.db.commit()
            
        except Exception as e:
            frappe.log_error(
                message=f"Error invalidating cache: {str(e)}", 
                title="Cache Error"
            )
            raise
