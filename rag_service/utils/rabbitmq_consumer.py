# rag_service/rag_service/utils/rabbitmq_consumer.py

import frappe
import pika
import json
from datetime import datetime
import asyncio
from typing import Dict, Optional
from ..handlers.feedback_handler import FeedbackHandler
from .queue_manager import QueueManager

class RabbitMQConsumer:
    def __init__(self, debug=True):
        self.settings = frappe.get_single("RabbitMQ Settings")
        self.queue_manager = QueueManager()
        self.feedback_handler = FeedbackHandler()
        self.debug = debug
        self.processed_count = 0
        self.connection = None
        self.channel = None

    def connect(self) -> None:
        """Establish RabbitMQ connection"""
        try:
            if self.debug:
                print(f"\nConnecting to RabbitMQ at {self.settings.host}...")
                
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
            
            if self.debug:
                print("Connection established successfully!")
                
        except Exception as e:
            error_msg = f"RabbitMQ Connection Error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "RabbitMQ Connection Error")
            raise

    def start_consuming(self) -> None:
        """Start consuming messages"""
        try:
            print("\n=== Starting RAG Service Consumer ===")
            
            self.connect()
            
            queue_name = self.settings.plagiarism_results_queue
            
            # Declare queue to ensure it exists
            self.channel.queue_declare(
                queue=queue_name,
                durable=True
            )
            
            # Get queue information
            queue_info = self.channel.queue_declare(
                queue=queue_name,
                durable=True,
                passive=True
            )
            
            message_count = queue_info.method.message_count
            print(f"\nFound {message_count} messages in queue '{queue_name}'")
            
            # Set up consumer
            self.channel.basic_qos(prefetch_count=1)
            self.channel.basic_consume(
                queue=queue_name,
                on_message_callback=self.process_message,
                auto_ack=False
            )
            
            print("\nWaiting for messages. To exit press CTRL+C")
            
            self.channel.start_consuming()
            
        except Exception as e:
            error_msg = f"Consumer Error: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Consumer Error")
            raise
        finally:
            if self.connection and not self.connection.is_closed:
                self.connection.close()

    def process_message(self, ch, method, properties, body) -> None:
        """Process incoming messages"""
        try:
            print(f"\n=== Processing Message {self.processed_count + 1} ===")
            
            # Print raw message for debugging
            if self.debug:
                print(f"Raw message: {body}")
            
            # Parse message
            try:
                message = json.loads(body)
                if self.debug:
                    print(f"Parsed JSON: {json.dumps(message, indent=2)}")
            except json.JSONDecodeError as e:
                print(f"JSON parsing error: {str(e)}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                print("Message rejected - Invalid JSON")
                return
                
            # Validate required fields
            required_fields = ['submission_id', 'student_id', 'assignment_id', 'img_url']
            missing_fields = [field for field in required_fields if field not in message]
            
            if missing_fields:
                print(f"Missing required fields: {missing_fields}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                print("Message rejected - Missing required fields")
                return
                
            # Process message using feedback handler
            try:
                # Create event loop for async operations
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    print("\nCalling feedback handler...")
                    loop.run_until_complete(
                        self.feedback_handler.handle_submission(message)
                    )
                    
                    # Acknowledge message
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    
                    # Update count
                    self.processed_count += 1
                    print(f"\nSuccessfully processed message {self.processed_count}")
                    
                finally:
                    loop.close()
                    
            except Exception as e:
                print(f"\nError processing submission: {str(e)}")
                frappe.log_error(
                    title="Submission Processing Error",
                    message=f"Error processing submission {message['submission_id']}: {str(e)}\n\nFull message: {json.dumps(message, indent=2)}"
                )
                # Requeue message for retry
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                
        except Exception as e:
            print(f"\nError processing message: {str(e)}")
            if self.debug:
                print(f"Message body: {body}")
            frappe.log_error(
                title="Message Processing Error",
                message=f"Error processing message: {str(e)}\n\nRaw message: {body}"
            )
            
            # Reject message without requeue
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            print("Message rejected")

    def test_connection(self) -> bool:
        """Test RabbitMQ connection"""
        try:
            self.connect()
            print("Connection test successful!")
            return True
        except Exception as e:
            print(f"Connection test failed: {str(e)}")
            return False
        finally:
            if self.connection and not self.connection.is_closed:
                self.connection.close()

    def verify_queues(self) -> None:
        """Verify RabbitMQ queue settings"""
        try:
            queue_status = self.queue_manager.verify_queues()
            
            print("\nQueue Status:")
            for queue, exists in queue_status.items():
                status = "✓ Available" if exists else "✗ Not Found"
                print(f"- {queue}: {status}")
                
            if all(queue_status.values()):
                print("\nAll queues verified successfully")
            else:
                print("\nSome queues are missing or inaccessible")
                
        except Exception as e:
            print(f"Queue verification failed: {str(e)}")

    def get_queue_status(self) -> Dict:
        """Get current queue statistics"""
        try:
            return self.queue_manager.monitor_queues()
        except Exception as e:
            print(f"Error getting queue status: {str(e)}")
            return {}
