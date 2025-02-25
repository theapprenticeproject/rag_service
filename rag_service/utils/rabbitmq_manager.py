import frappe
import pika
import json
import time
from datetime import datetime
from typing import Dict, Optional, Union

class RabbitMQManager:
    def __init__(self, debug=True):
        self.settings = frappe.get_single("RabbitMQ Settings")
        self.debug = debug
        self.connection = None
        self.channel = None
        self.processed_count = 0

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

    def close(self) -> None:
        """Close RabbitMQ connection"""
        if self.connection and not self.connection.is_closed:
            self.connection.close()
            self.connection = None
            self.channel = None

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
            self.close()

    def get_queue_info(self, queue_name: str = None) -> Dict:
        """Get information about a specific queue or all queues"""
        try:
            self.connect()
            
            if queue_name is None:
                queue_name = self.settings.plagiarism_results_queue
                
            queue_info = self.channel.queue_declare(
                queue=queue_name,
                durable=True,
                passive=True
            )
            
            return {
                'queue': queue_name,
                'message_count': queue_info.method.message_count,
                'consumer_count': queue_info.method.consumer_count
            }
            
        except Exception as e:
            print(f"Error getting queue info: {str(e)}")
            return {}
        finally:
            self.close()

    def peek_message(self, queue_name: str = None) -> Optional[dict]:
        """Peek at the next message in the queue without consuming it"""
        try:
            self.connect()
            
            if queue_name is None:
                queue_name = self.settings.plagiarism_results_queue
                
            # Get message without consuming
            method_frame, header_frame, body = self.channel.basic_get(
                queue=queue_name,
                auto_ack=False
            )
            
            if not method_frame:
                print("\nNo messages in queue")
                return None
                
            try:
                message = json.loads(body)
                result = {
                    'delivery_tag': method_frame.delivery_tag,
                    'content': message,
                    'raw_body': body.decode('utf-8')
                }
            except json.JSONDecodeError as e:
                result = {
                    'delivery_tag': method_frame.delivery_tag,
                    'error': f"Invalid JSON: {str(e)}",
                    'raw_body': body.decode('utf-8')
                }
            
            # Return message to queue
            self.channel.basic_nack(
                delivery_tag=method_frame.delivery_tag,
                requeue=True
            )
            
            return result
            
        except Exception as e:
            print(f"Error peeking message: {str(e)}")
            return None
        finally:
            self.close()

    def delete_message(self, delivery_tag: int, queue_name: str = None) -> bool:
        """Delete a specific message using its delivery tag"""
        try:
            self.connect()
            
            if queue_name is None:
                queue_name = self.settings.plagiarism_results_queue
                
            # Get and reject the message
            method_frame, _, body = self.channel.basic_get(
                queue=queue_name,
                auto_ack=False
            )
            
            if not method_frame:
                print("\nNo messages in queue")
                return False
                
            if method_frame.delivery_tag == delivery_tag:
                self.channel.basic_reject(
                    delivery_tag=delivery_tag,
                    requeue=False
                )
                print(f"\nSuccessfully deleted message with delivery tag: {delivery_tag}")
                return True
            else:
                print(f"\nMessage with delivery tag {delivery_tag} not found at queue head")
                # Return message to queue
                self.channel.basic_nack(
                    delivery_tag=method_frame.delivery_tag,
                    requeue=True
                )
                return False
                
        except Exception as e:
            error_msg = f"Error deleting message: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Message Deletion Error")
            return False
        finally:
            self.close()

    def peek_and_delete(self, queue_name: str = None) -> dict:
        """Peek at the next message and optionally delete it"""
        result = {
            'success': False,
            'message': None,
            'action_taken': None
        }
        
        # First peek at the message
        message_info = self.peek_message(queue_name)
        
        if not message_info:
            result['message'] = "No messages in queue"
            return result
            
        # Show the message content
        print("\nNext message in queue:")
        print(f"Delivery Tag: {message_info['delivery_tag']}")
        print(f"Raw content: {message_info['raw_body']}")
        
        if 'error' in message_info:
            print(f"Parse error: {message_info['error']}")
        else:
            print(f"Parsed content: {json.dumps(message_info['content'], indent=2)}")
            
        # Ask for confirmation
        confirm = input("\nDo you want to delete this message? (y/n): ").lower()
        
        if confirm == 'y':
            success = self.delete_message(message_info['delivery_tag'], queue_name)
            result.update({
                'success': success,
                'message': "Message deleted successfully" if success else "Failed to delete message",
                'action_taken': 'deleted'
            })
        else:
            result.update({
                'success': True,
                'message': "Message left in queue",
                'action_taken': 'skipped'
            })
            
        return result

    def purge_queue(self, queue_name: str = None) -> bool:
        """Purge all messages from a queue"""
        try:
            self.connect()
            
            if queue_name is None:
                queue_name = self.settings.plagiarism_results_queue
                
            # Get message count before purging
            queue_info = self.channel.queue_declare(
                queue=queue_name,
                durable=True,
                passive=True
            )
            message_count = queue_info.method.message_count
            
            # Confirm purge
            confirm = input(f"\nAre you sure you want to purge {message_count} messages from queue '{queue_name}'? (y/n): ").lower()
            
            if confirm == 'y':
                self.channel.queue_purge(queue=queue_name)
                print(f"\nSuccessfully purged {message_count} messages from queue: {queue_name}")
                return True
            else:
                print("\nPurge cancelled")
                return False
                
        except Exception as e:
            error_msg = f"Error purging queue: {str(e)}"
            print(f"\nError: {error_msg}")
            frappe.log_error(error_msg, "Queue Purge Error")
            return False
        finally:
            self.close()
