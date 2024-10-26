# file: rag_service/rag_service/rabbitmq_utils.py

import frappe
import pika
import json
from .feedback_generator import generate_feedback

def get_rabbitmq_settings():
    return frappe.get_single("RabbitMQ Settings")

def connect_to_rabbitmq():
    settings = get_rabbitmq_settings()
    credentials = pika.PlainCredentials(settings.username, settings.password)
    parameters = pika.ConnectionParameters(settings.host,
                                           settings.port,
                                           settings.virtual_host,
                                           credentials)
    return pika.BlockingConnection(parameters)

def process_plagiarism_result(ch, method, properties, body):
    try:
        data = json.loads(body)
        
        # Create a new Feedback Request
        feedback_request = frappe.get_doc({
            "doctype": "Feedback Request",
            "request_id": data.get("submission_id"),
            "student_id": data.get("student_id"),
            "assignment_id": data.get("assignment_id"),
            "submission_id": data.get("submission_id"),
            "plagiarism_score": data.get("plagiarism_score"),
            "similar_sources": json.dumps(data.get("similar_sources")),
            "status": "Pending"
        })
        feedback_request.insert()
        
        # Generate feedback
        generate_feedback(feedback_request.name)
        
    except Exception as e:
        frappe.log_error(f"Error processing plagiarism result: {str(e)}")

def start_consuming():
    connection = connect_to_rabbitmq()
    channel = connection.channel()
    
    settings = get_rabbitmq_settings()
    channel.queue_declare(queue=settings.plagiarism_results_queue, durable=True)
    channel.basic_consume(queue=settings.plagiarism_results_queue, 
                          on_message_callback=process_plagiarism_result, 
                          auto_ack=True)
    
    print("Starting to consume messages...")
    channel.start_consuming()

# Add this to your hooks.py file to start the consumer when the app starts
# app_init = "rag_service.rag_service.rabbitmq_utils.start_consuming"
