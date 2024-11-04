# File: ~/frappe-bench/apps/rag_service/rag_service/utils/setup_test_data.py

import frappe
from frappe.utils import now_datetime
from ..core.embedding_utils import embedding_manager

def create_test_data():
    """Create test data for RAG system"""
    test_contents = [
        {
            "content": "Python is a high-level programming language known for its simplicity and readability.",
            "reference_id": "test_content_1",
            "content_type": "reference"  # Changed to lowercase
        },
        {
            "content": "Machine learning is a subset of artificial intelligence that enables systems to learn and improve from experience.",
            "reference_id": "test_content_2",
            "content_type": "reference"
        },
        {
            "content": "Natural Language Processing (NLP) helps computers understand and process human language.",
            "reference_id": "test_content_3",
            "content_type": "reference"
        },
        {
            "content": "This is a sample student submission about programming concepts.",
            "reference_id": "submission_1",
            "content_type": "submission"
        },
        {
            "content": "Great work on explaining the concepts. Consider adding more examples.",
            "reference_id": "feedback_1",
            "content_type": "feedback"
        }
    ]

    created_docs = []
    
    try:
        for content in test_contents:
            # Check if already exists
            existing = frappe.db.exists(
                "Vector Store",
                {"reference_id": content["reference_id"]}
            )
            
            if not existing:
                # Create embedding and save
                vector_store_name = embedding_manager.save_embedding(
                    reference_id=content["reference_id"],
                    content=content["content"],
                    content_type=content["content_type"]
                )
                created_docs.append(vector_store_name)
                print(f"Created: {vector_store_name} - {content['content_type']}")
                
        frappe.db.commit()
        return f"Created {len(created_docs)} test documents: {', '.join(created_docs)}"
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error creating test data")
        return f"Error creating test data: {str(e)}"

def verify_test_data():
    """Verify the created test data"""
    try:
        vector_stores = frappe.get_all(
            "Vector Store",
            fields=["name", "content_type", "reference_id", "content", "embedding_file"],
            order_by="creation desc"
        )
        
        print(f"\nFound {len(vector_stores)} Vector Store entries:")
        for vs in vector_stores:
            print("\nVector Store:", vs.name)
            print("Type:", vs.content_type)
            print("Reference:", vs.reference_id)
            print("Content:", vs.content)
            print("Embedding File:", vs.embedding_file)
            
        return vector_stores
        
    except Exception as e:
        print(f"Error verifying test data: {str(e)}")
        return None

# You can run this from bench console
if __name__ == "__main__":
    create_test_data()
