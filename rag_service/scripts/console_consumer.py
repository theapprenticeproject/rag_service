from rag_service.utils.rabbitmq_consumer import RabbitMQConsumer


def start_consumer():
    consumer = RabbitMQConsumer(debug=True)

    if not consumer.test_connection():
        raise RuntimeError("RabbitMQ connection test failed")

    print("Starting RabbitMQ consumer...", flush=True)

    try:
        consumer.start_consuming()
    except KeyboardInterrupt:
        print("RabbitMQ consumer stopped.", flush=True)


if __name__ == "__main__":
    start_consumer()
