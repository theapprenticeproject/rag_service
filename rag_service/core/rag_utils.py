# File: ~/frappe-bench/apps/rag_service/rag_service/core/rag_utils.py

import frappe
import json
from .embedding_utils import embedding_manager
from .vector_store import faiss_manager
from .feedback_generator import feedback_generator

def process_submission(submission_id, content):
    """Process a new submission through the RAG pipeline"""
    try:
        # Generate and store embedding
        vector_store_name = embedding_manager.save_embedding(
            reference_id=submission_id,
            content=content,
            content_type="submission"
        )
        
        # Add to FAISS index
        faiss_manager.add_vector(vector_store_name)
        
        # Find similar submissions
        embedding = embedding_manager.load_embedding(vector_store_name)
        similar_submissions = faiss_manager.search_similar(embedding)
        
        return {
            "vector_store_name": vector_store_name,
            "similar_submissions": similar_submissions,
            "plagiarism_score": min([s['distance'] for s in similar_submissions]) if similar_submissions else 0
        }
        
    except Exception as e:
        frappe.log_error(f"Error processing submission: {str(e)}")
        raise

def find_similar_content(query_text, k=5):
    """Find similar content for given text"""
    try:
        # Generate embedding for query
        query_embedding = embedding_manager.generate_embedding(query_text)
        
        # Search for similar content
        similar_content = faiss_manager.search_similar(query_embedding, k)
        
        # Get full content for results
        results = []
        for item in similar_content:
            vector_store = frappe.get_doc("Vector Store", item["vector_store"])
            results.append({
                "content": vector_store.content,
                "content_type": vector_store.content_type,
                "reference_id": vector_store.reference_id,
                "similarity_score": item["distance"]
            })
        
        return results
        
    except Exception as e:
        frappe.log_error(f"Error finding similar content: {str(e)}")
        raise

def generate_feedback(submission_id, content):
    """Generate feedback for a submission"""
    try:
        # Process submission and get similar content
        process_result = process_submission(submission_id, content)
        
        # Get similar contents with their full details
        similar_contents = find_similar_content(content)
        
        # Generate feedback
        feedback_result = feedback_generator.generate_structured_feedback(
            content,
            similar_contents,
            plagiarism_score=process_result.get("plagiarism_score", None)
        )
        
        # Store feedback in Vector Store
        feedback_store_name = embedding_manager.save_embedding(
            reference_id=f"feedback_{submission_id}",
            content=feedback_result["feedback"],
            content_type="feedback"
        )
        
        return {
            "feedback": feedback_result["feedback"],
            "metadata": feedback_result["metadata"],
            "similar_contents": similar_contents[:3],  # Top 3 similar contents
            "feedback_id": feedback_store_name
        }
        
    except Exception as e:
        frappe.log_error(f"Error generating feedback: {str(e)}")
        raise
