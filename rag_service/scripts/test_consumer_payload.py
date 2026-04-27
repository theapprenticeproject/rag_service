#!/usr/bin/env python3
"""
Test script to send payloads directly to the RabbitMQ consumer for testing.
Simply modify the payload dictionary below and run: python test_consumer_payload.py
"""

import sys
import json
import pika
from datetime import datetime
from pathlib import Path

# Add the apps directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

image_5 = "https://storage.googleapis.com/bucket_tap_1/uploads/21/09/23_Sibling_issue/20251105064001_C107277_F32580_M18081088.png"
image_1 = "https://storage.googleapis.com/bucket_tap_1/uploads/added_in_coll/20251007160901_C107788_F32580_M16096985.png"
image_1_digital = "https://storage.googleapis.com/bucket_tap_1/uploads/11/AugProccess/20251007114200_C128977_F32580_M16044526.png"
image_1_cropped = "https://storage.googleapis.com/bucket_tap_1/uploads/21/09/23_Sibling_issue/20251008024900_C132450_F32580_M16113857.png"

video_original = "https://storage.googleapis.com/bucket_tap_1/uploads/Activity2_B1_Issue(Retrigger)/20251104121000_C445224_F32580_M17966712.mp4"

# ============================================================================
# EDIT THE PAYLOAD BELOW TO TEST DIFFERENT DATA
# ============================================================================
PAYLOAD = {
  "submission_id": "IMSUB-26021704659",
  "student_id": "ST00000206",
  "img_url": video_original,
  "created_at": "2026-02-17 22:32:44.472760",
  "similar_sources": None,
  "similarity_score": 1.0,
  "is_plagiarized": False,
  "match_type": "original",
  "assignment_id": "VA_L2_CA1-Basic",
  "is_ai_generated": False,
  "ai_detection_source": "None",
  "ai_confidence": 0.0,
  "plagiarism_source": None
}
# ============================================================================



def get_rabbitmq_settings():
    """Get RabbitMQ settings from Frappe"""
    try:
        return {
            "host": "rabbit-01.lmq.cloudamqp.com",
            "port": "5672",
            "username": "aoafhbrm",
            "password": "****",
            "virtual_host": "aoafhbrm",
            "queue": "plg_result_q_local",
        }
    except Exception as e:
        print(f"Error fetching RabbitMQ settings: {e}")
        return None


def connect_to_rabbitmq(settings):
    """Establish connection to RabbitMQ"""
    try:
        credentials = pika.PlainCredentials(settings["username"], settings["password"])
        parameters = pika.ConnectionParameters(
            host=settings["host"],
            port=settings["port"],
            virtual_host=settings["virtual_host"],
            credentials=credentials,
            heartbeat=600,
            blocked_connection_timeout=300,
        )
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        print(f"\n✓ Connected to RabbitMQ at {settings['host']}:{settings['port']}")
        return connection, channel
    except Exception as e:
        print(f"\n✗ RabbitMQ Connection Error: {e}")
        return None, None


def send_payload(connection, channel, queue_name, payload):
    """Send a payload to RabbitMQ queue"""
    try:
        # Declare queue to ensure it exists
        channel.queue_declare(queue=queue_name, durable=True)
        
        # Send message
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=json.dumps(payload, ensure_ascii=False),
            properties=pika.BasicProperties(
                delivery_mode=2,  # persistent
                content_type="application/json",
            ),
        )
        print(f"\n✓ Payload sent to queue '{queue_name}'")
        print(f"  Submission ID: {payload.get('submission_id')}")
        print(f"  Student ID: {payload.get('student_id')}")
        print(f"  Assignment ID: {payload.get('assignment_id')}")
        return True
    except Exception as e:
        print(f"\n✗ Error sending payload: {e}")
        return False
    finally:
        if connection and not connection.is_closed:
            connection.close()


def main():
    # Get RabbitMQ settings
    settings = get_rabbitmq_settings()
    if not settings:
        print("\n✗ Unable to retrieve RabbitMQ settings")
        return

    print(f"\n=== RabbitMQ Consumer Test Script ===")
    print(f"Host: {settings['host']}")
    print(f"Port: {settings['port']}")
    print(f"Queue: {settings['queue']}")

    # Connect to RabbitMQ
    connection, channel = connect_to_rabbitmq(settings)
    if not connection or not channel:
        return

    try:
        # Validate required fields
        required_fields = ["submission_id", "student_id", "assignment_id", "img_url"]
        missing = [f for f in required_fields if f not in PAYLOAD]
        if missing:
            print(f"\n✗ Missing required fields: {', '.join(missing)}")
            return

        print(f"\n--- Sending Payload ---")
        print(f"Payload:\n{json.dumps(PAYLOAD, indent=2)}")
        send_payload(connection, channel, settings["queue"], PAYLOAD)

    finally:
        if connection and not connection.is_closed:
            connection.close()
            print("\n✓ Disconnected from RabbitMQ")


if __name__ == "__main__":
    main()
