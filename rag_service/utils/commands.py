# apps/rag_service/rag_service/utils/commands.py

import frappe
from frappe.commands import pass_context
import click

@click.command('rag-consumer-start')
@pass_context
def start_consumer(context):
    """Start the RAG Service consumer"""
    from rag_service.rag_service.utils.rabbitmq_consumer import RabbitMQConsumer
    
    site = context.sites[0]
    frappe.init(site=site)
    frappe.connect()
    
    try:
        click.echo(f"Starting RAG consumer for site: {site}")
        consumer = RabbitMQConsumer()
        consumer.start_consuming()
    except Exception as e:
        click.echo(f"Error starting consumer: {str(e)}")
    finally:
        frappe.destroy()

# And update hooks.py:
commands = [
    "rag_service.utils.commands.start_consumer"
]
