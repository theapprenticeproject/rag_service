# rag_service/rag_service/utils/queue_manager.py

import frappe
import pika
import json
from typing import Dict, Optional
from datetime import datetime

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
            
            # Declare queues to ensure they exist
            self.channel.queue_declare(
                queue=self.settings.plagiarism_results_queue,
                durable=True
            )
            self.channel.queue_declare(
                queue=self.settings.feedback_results_queue,
                durable=True
            )
            
            print("\nConnected to RabbitMQ successfully")
            
        except Exception as e:
            error_msg = f"RabbitMQ Connection Error: {str(e)}"
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
            print("\n=== Sending Feedback to TAP LMS ===")
            print(f"Queue: {self.settings.feedback_results_queue}")
            
            self.connect()
            
            # Add metadata to feedback
            message = {
                **feedback_data,
                "sent_at": datetime.now().isoformat(),
                "service": "RAG"
            }
            
            # Send to queue
            self.channel.basic_publish(
                exchange='',
                routing_key=self.settings.feedback_results_queue,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # make message persistent
                    content_type='application/json'
                )
            )
            
            print(f"\nFeedback sent successfully for submission: {feedback_data.get('submission_id')}")
            
        except Exception as e:
            error_msg = f"Error sending feedback to TAP LMS: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Feedback Delivery Error")
            raise
        finally:
            self.disconnect()

    def verify_queues(self) -> Dict[str, bool]:
        """Verify all required queues exist"""
        try:
            self.connect()
            queue_status = {}
            
            queues_to_check = [
                self.settings.plagiarism_results_queue,
                self.settings.feedback_results_queue
            ]
            
            for queue_name in queues_to_check:
                try:
                    self.channel.queue_declare(
                        queue=queue_name,
                        durable=True,
                        passive=True  # Only check if exists
                    )
                    queue_status[queue_name] = True
                except Exception:
                    queue_status[queue_name] = False
                    
            return queue_status
            
        except Exception as e:
            error_msg = f"Queue verification error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Queue Verification Error")
            raise
        finally:
            self.disconnect()

    def purge_queue(self, queue_name: str) -> None:
        """Purge all messages from a queue (use with caution)"""
        try:
            self.connect()
            self.channel.queue_purge(queue=queue_name)
            print(f"\nPurged queue: {queue_name}")
        except Exception as e:
            error_msg = f"Queue purge error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Queue Purge Error")
            raise
        finally:
            self.disconnect()

    def get_queue_info(self, queue_name: str) -> Dict:
        """Get information about a queue"""
        try:
            self.connect()
            response = self.channel.queue_declare(
                queue=queue_name,
                durable=True,
                passive=True
            )
            
            return {
                "queue": queue_name,
                "message_count": response.method.message_count,
                "consumer_count": response.method.consumer_count
            }
            
        except Exception as e:
            error_msg = f"Queue info error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Queue Info Error")
            raise
        finally:
            self.disconnect()

    def monitor_queues(self) -> Dict:
        """Monitor all queues"""
        try:
            queues = [
                self.settings.plagiarism_results_queue,
                self.settings.feedback_results_queue
            ]
            
            queue_stats = {}
            for queue in queues:
                queue_stats[queue] = self.get_queue_info(queue)
                
            return queue_stats
            
        except Exception as e:
            error_msg = f"Queue monitoring error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Queue Monitoring Error")
            raise
