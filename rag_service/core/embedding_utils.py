# File: ~/frappe-bench/apps/rag_service/rag_service/core/embedding_utils.py

import frappe
from sentence_transformers import SentenceTransformer
import numpy as np
import os
from frappe.utils import now_datetime
import json

class EmbeddingManager:
    def __init__(self):
        self.model = None
        self.model_name = 'all-MiniLM-L6-v2'
        self.embedding_dimension = 384
        
    def get_model(self):
        if self.model is None:
            self.model = SentenceTransformer(self.model_name)
        return self.model
    
    def generate_embedding(self, text):
        """Generate embedding for given text"""
        model = self.get_model()
        embedding = model.encode(text)
        return embedding
    
    def save_embedding(self, reference_id, content, content_type="Submission"):
        """Save embedding to Vector Store"""
        try:
            # Generate embedding
            embedding = self.generate_embedding(content)
            
            # Create a file path for the embedding
            site_path = frappe.get_site_path()
            embedding_dir = os.path.join(site_path, 'private', 'files', 'embeddings')
            os.makedirs(embedding_dir, exist_ok=True)
            
            file_path = os.path.join(embedding_dir, f"{content_type}_{reference_id}_{now_datetime().strftime('%Y%m%d_%H%M%S')}.npy")
            
            # Save the embedding to file
            np.save(file_path, embedding)
            
            # Create Vector Store entry
            vector_store = frappe.get_doc({
                "doctype": "Vector Store",
                "content_type": content_type,
                "reference_id": reference_id,
                "content": content,
                "embedding_file": os.path.relpath(file_path, site_path),
                "created_at": now_datetime()
            })
            
            vector_store.insert()
            frappe.db.commit()
            
            return vector_store.name
            
        except Exception as e:
            frappe.log_error(f"Error saving embedding: {str(e)}")
            raise
    
    def load_embedding(self, vector_store_name):
        """Load embedding from Vector Store"""
        try:
            vector_store = frappe.get_doc("Vector Store", vector_store_name)
            embedding_path = os.path.join(frappe.get_site_path(), vector_store.embedding_file)
            
            if not os.path.exists(embedding_path):
                frappe.throw(f"Embedding file not found: {embedding_path}")
                
            embedding = np.load(embedding_path)
            return embedding
            
        except Exception as e:
            frappe.log_error(f"Error loading embedding: {str(e)}")
            raise

# Create a singleton instance
embedding_manager = EmbeddingManager()
