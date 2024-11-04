# File: rag_service/rag_service/rabbitmq_utils.py

import frappe
import pika
import json
from .core.rag_utils import process_submission, find_similar_content

def process_message(ch, method, properties, body):
    try:
        message_data = json.loads(body)
        
        # Process the submission
        result = process_submission(
            message_data.get("submission_id"),
            message_data.get("content")
        )
        
        # Create feedback request
        feedback_request = frappe.get_doc({
            "doctype": "Feedback Request",
            "request_id": message_data.get("submission_id"),
            "student_id": message_data.get("student_id"),
            "assignment_id": message_data.get("assignment_id"),
            "submission_content": message_data.get("content"),
            "plagiarism_score": message_data.get("plagiarism_score"),
            "status": "Processing",
            "similar_submissions": json.dumps(result.get("similar_submissions"))
        })
        
        feedback_request.insert()
        frappe.db.commit()
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error processing RabbitMQ message")


def start_consuming():
    try:
        settings = get_rabbitmq_settings()
        
        # Log the connection attempt
        frappe.logger().info(f"Connecting to RabbitMQ at {settings.host}:{settings.port}")
        
        # Create connection
        credentials = pika.PlainCredentials(settings.username, settings.password)
        parameters = pika.ConnectionParameters(
            host=settings.host,
            port=settings.port,
            virtual_host=settings.virtual_host,
            credentials=credentials
        )
        
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        
        # Ensure queue exists
        channel.queue_declare(queue=settings.plagiarism_results_queue, durable=True)
        
        # Set up consumer
        channel.basic_consume(
            queue=settings.plagiarism_results_queue,
            on_message_callback=process_message,
            auto_ack=True
        )
        
        frappe.logger().info("Started consuming messages...")
        channel.start_consuming()
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error in RabbitMQ consumer")
        raise

