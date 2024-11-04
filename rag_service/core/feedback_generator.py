# File: ~/frappe-bench/apps/rag_service/rag_service/core/feedback_generator.py

import frappe
from .embedding_utils import embedding_manager
from .vector_store import faiss_manager
import json
from datetime import datetime

class FeedbackGenerator:
    def __init__(self):
        self.feedback_templates = {
            "general": """
Based on the submission content and similar examples, here's the feedback:

Strengths:
{strengths}

Areas for Improvement:
{improvements}

Suggestions:
{suggestions}

Overall Assessment:
{assessment}
            """.strip(),
            
            "plagiarism_alert": """
⚠️ Plagiarism Concern:
The submission shows significant similarity ({similarity_score:.2f}%) with existing content.
Please review and ensure original work.

Similar Content Found:
{similar_content}

Recommendation:
{recommendation}
            """.strip()
        }

    def generate_structured_feedback(self, submission_content, similar_contents, plagiarism_score=None):
        """Generate structured feedback using RAG approach"""
        try:
            # Analyze strengths
            strengths = self._analyze_strengths(submission_content)
            
            # Analyze areas for improvement
            improvements = self._analyze_improvements(submission_content, similar_contents)
            
            # Generate suggestions
            suggestions = self._generate_suggestions(submission_content, similar_contents)
            
            # Create feedback
            feedback = self.feedback_templates["general"].format(
                strengths="\n".join(f"- {s}" for s in strengths),
                improvements="\n".join(f"- {i}" for i in improvements),
                suggestions="\n".join(f"- {s}" for s in suggestions),
                assessment=self._create_overall_assessment(
                    submission_content, 
                    strengths, 
                    improvements
                )
            )
            
            # Add plagiarism warning if score is high
            if plagiarism_score and plagiarism_score > 0.8:
                feedback += "\n\n" + self.feedback_templates["plagiarism_alert"].format(
                    similarity_score=plagiarism_score * 100,
                    similar_content=self._format_similar_content(similar_contents[:1]),
                    recommendation="Please revise your submission to ensure originality."
                )
            
            return {
                "feedback": feedback,
                "metadata": {
                    "strengths_count": len(strengths),
                    "improvements_count": len(improvements),
                    "suggestions_count": len(suggestions),
                    "has_plagiarism_warning": plagiarism_score > 0.8 if plagiarism_score else False,
                    "generated_at": str(datetime.now())
                }
            }
            
        except Exception as e:
            frappe.log_error(f"Error generating feedback: {str(e)}")
            raise

    def _analyze_strengths(self, content):
        """Analyze submission strengths"""
        # Placeholder - Implement actual strength analysis
        strengths = [
            "Clear presentation of concepts",
            "Good structure and organization",
            "Effective use of examples"
        ]
        return strengths

    def _analyze_improvements(self, content, similar_contents):
        """Analyze areas for improvement"""
        # Placeholder - Implement actual improvement analysis
        improvements = [
            "Consider adding more detailed explanations",
            "Include more specific examples",
            "Expand on key concepts"
        ]
        return improvements

    def _generate_suggestions(self, content, similar_contents):
        """Generate specific suggestions"""
        # Placeholder - Implement actual suggestion generation
        suggestions = [
            "Reference related topics to strengthen understanding",
            "Include practical applications of concepts",
            "Add visual representations where applicable"
        ]
        return suggestions

    def _create_overall_assessment(self, content, strengths, improvements):
        """Create overall assessment"""
        # Placeholder - Implement actual assessment logic
        return "The submission demonstrates good understanding of the concepts while having room for enhancement in specific areas."

    def _format_similar_content(self, similar_contents):
        """Format similar content for feedback"""
        if not similar_contents:
            return "No similar content found"
            
        formatted = []
        for content in similar_contents:
            formatted.append(f"- Content: {content['content']}\n  Similarity: {content['similarity_score']:.2%}")
            
        return "\n".join(formatted)

# Create singleton instance
feedback_generator = FeedbackGenerator()
