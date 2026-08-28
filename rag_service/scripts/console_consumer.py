def start_consumer():
    from rag_service.utils.rabbitmq_consumer import RabbitMQConsumer

    consumer = RabbitMQConsumer(debug=True)

    if not consumer.test_connection():
        raise RuntimeError("RabbitMQ connection test failed")

    print("Starting RabbitMQ consumer...", flush=True)

    try:
        consumer.start_consuming()
    except KeyboardInterrupt:
        print("RabbitMQ consumer stopped.", flush=True)

# This file is intended to be pasted into bench console, where __name__ is
# not a reliable trigger. Start explicitly after defining the helper.
start_consumer()
