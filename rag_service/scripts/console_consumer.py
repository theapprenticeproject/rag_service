from rag_service.utils.rabbitmq_consumer import RabbitMQConsumer
consumer = RabbitMQConsumer(debug=True)
if consumer.test_connection():
    consumer.start_consuming()