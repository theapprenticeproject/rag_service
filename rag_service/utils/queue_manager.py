# rag_service/rag_service/utils/queue_manager.py

import frappe
import pika
import json
from typing import Dict
from datetime import datetime
import traceback

class QueueManager:
    def __init__(self):
        self.settings = frappe.get_single("RabbitMQ Settings")
        self.connection = None
        self.channel = None

    def connect(self) -> None:
        """Establish connection to RabbitMQ"""
        try:
            if self.connection and not self.connection.is_closed:
                return

            credentials = pika.PlainCredentials(
                self.settings.username,
                self.settings.password
            )
            
            parameters = pika.ConnectionParameters(
                host=self.settings.host,
                port=int(self.settings.port),
                virtual_host=self.settings.virtual_host,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300
            )
            
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            self.channel.confirm_delivery()
            
            # Queue exists and topology is managed by another service (LMS)
            print(f"Connected to RabbitMQ. Will publish to queue: {self.settings.feedback_results_queue}")            
        except Exception as e:
            error_msg = f"RabbitMQ Connection Error for producer: {str(e)}\n{traceback.format_exc()}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "RabbitMQ Connection Error")
            raise

    def disconnect(self) -> None:
        """Close RabbitMQ connection"""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                print("\nDisconnected from RabbitMQ")
        except Exception as e:
            print(f"\nError disconnecting: {str(e)}")

    def send_feedback_to_tap(self, feedback_data: Dict) -> None:
        """Send feedback to TAP LMS queue"""
        try:
            print(f"Queue: {self.settings.feedback_results_queue}")
            
            self.connect()
            
            # Add metadata to feedback
            message = {
                **feedback_data,
                "sent_at": datetime.now().isoformat(),
                "service": "RAG"
            }
            
            # Send to queue
            confirmed = self.channel.basic_publish(
                exchange='',
                routing_key=self.settings.feedback_results_queue,
                body=json.dumps(message, ensure_ascii=False),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # make message persistent
                    content_type='application/json'
                ),
                mandatory=True
            )
            if confirmed is False:
                raise RuntimeError("RabbitMQ did not confirm feedback publish")
            
            print(f"\nFeedback sent successfully for submission: {feedback_data.get('submission_id')}")
            
        except Exception as e:
            error_msg = f"Error sending feedback to TAP LMS: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Feedback Delivery Error")
            raise
        finally:
            self.disconnect()
