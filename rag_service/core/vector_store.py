# File: rag_service/rag_service/core/vector_store.py

import frappe
import faiss
import numpy as np
import os
from .embedding_utils import embedding_manager

class FAISSManager:
    def __init__(self):
        self.index = None
        self.dimension = embedding_manager.embedding_dimension
        self.vector_ids = []  # To maintain mapping between FAISS and Vector Store
        
    def initialize_index(self):
        """Initialize FAISS index"""
        if self.index is None:
            self.index = faiss.IndexFlatL2(self.dimension)
            self._load_existing_vectors()
    
    def _load_existing_vectors(self):
        """Load existing vectors from Vector Store into FAISS index"""
        try:
            vector_stores = frappe.get_all(
                "Vector Store",
                fields=["name", "embedding_file"]
            )
            
            vectors = []
            for vs in vector_stores:
                try:
                    embedding = embedding_manager.load_embedding(vs.name)
                    vectors.append(embedding)
                    self.vector_ids.append(vs.name)
                except Exception as e:
                    frappe.log_error(f"Error loading vector {vs.name}: {str(e)}")
            
            if vectors:
                vectors_array = np.array(vectors).astype('float32')
                self.index.add(vectors_array)
                
        except Exception as e:
            frappe.log_error(f"Error loading existing vectors: {str(e)}")
    
    def add_vector(self, vector_store_name):
        """Add a new vector to the index"""
        try:
            self.initialize_index()
            
            embedding = embedding_manager.load_embedding(vector_store_name)
            self.index.add(embedding.reshape(1, -1).astype('float32'))
            self.vector_ids.append(vector_store_name)
            
        except Exception as e:
            frappe.log_error(f"Error adding vector to FAISS: {str(e)}")
            raise
    
    def search_similar(self, query_vector, k=5):
        """Search for similar vectors"""
        try:
            self.initialize_index()
            
            distances, indices = self.index.search(
                query_vector.reshape(1, -1).astype('float32'),
                k
            )
            
            results = []
            for i, idx in enumerate(indices[0]):
                if idx < len(self.vector_ids):
                    vector_store = frappe.get_doc("Vector Store", self.vector_ids[idx])
                    results.append({
                        "vector_store": vector_store.name,
                        "reference_doctype": vector_store.reference_doctype,
                        "reference_name": vector_store.reference_name,
                        "distance": float(distances[0][i])
                    })
            
            return results
            
        except Exception as e:
            frappe.log_error(f"Error searching similar vectors: {str(e)}")
            raise

# Create a singleton instance
faiss_manager = FAISSManager()
