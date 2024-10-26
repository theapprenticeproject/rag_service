# file: rag_service/rag_service/feedback_generator.py

import frappe
from langchain import OpenAI, LLMChain
from langchain.prompts import PromptTemplate

def get_student_context(student_id):
    return frappe.get_doc("Student Context", student_id)

def get_assignment_context(assignment_id):
    return frappe.get_doc("Assignment Context", assignment_id)

def get_feedback_template(course_vertical, assignment_type, skill_level):
    return frappe.get_doc("Feedback Template", {
        "course_vertical": course_vertical,
        "assignment_type": assignment_type,
        "skill_level": skill_level,
        "active": 1
    })

def generate_feedback(feedback_request_id):
    feedback_request = frappe.get_doc("Feedback Request", feedback_request_id)
    student_context = get_student_context(feedback_request.student_id)
    assignment_context = get_assignment_context(feedback_request.assignment_id)
    
    template = get_feedback_template(
        assignment_context.course_vertical,
        assignment_context.assignment_type,
        student_context.skill_level
    )
    
    prompt = PromptTemplate(
        input_variables=["assignment", "submission", "rubric", "plagiarism_score"],
        template=template.template_content
    )
    
    llm = OpenAI(temperature=0.7)
    chain = LLMChain(llm=llm, prompt=prompt)
    
    feedback = chain.run({
        "assignment": assignment_context.assignment_name,
        "submission": feedback_request.submission_content,
        "rubric": assignment_context.rubric,
        "plagiarism_score": feedback_request.plagiarism_score
    })
    
    feedback_request.generated_feedback = feedback
    feedback_request.status = "Completed"
    feedback_request.save()
    
    # Send feedback back to TAP LMS (implement this in the next step)
    send_feedback_to_tap_lms(feedback_request)

def send_feedback_to_tap_lms(feedback_request):
    # Implementation for sending feedback back to TAP LMS via RabbitMQ
    # We'll implement this in the next step
    pass
