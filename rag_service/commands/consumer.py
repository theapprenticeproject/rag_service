# rag_service/rag_service/commands/consumer.py

import click
import frappe
from frappe.commands import pass_context
from rag_service.rag_service.utils.rabbitmq_consumer import RabbitMQConsumer

@click.command('start-rag-consumer')
@pass_context
def start_consumer(context):
    """Start the RAG Service RabbitMQ consumer"""
    site = context.sites[0]
    frappe.init(site=site)
    frappe.connect()
    
    try:
        consumer = RabbitMQConsumer()
        consumer.start_consuming()
    except Exception as e:
        click.echo(f"Error starting consumer: {str(e)}")
    finally:
        frappe.destroy()

commands = [
    start_consumer
]
