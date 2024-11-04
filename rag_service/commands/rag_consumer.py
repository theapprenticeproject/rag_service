# apps/rag_service/rag_service/commands/rag_consumer.py

import click
import frappe
from frappe.commands import pass_context, get_site

def create_command(command_name, help_text=None):
    """Helper function to create click commands"""
    def decorator(func):
        @click.command(command_name, help=help_text)
        @click.argument('site')
        def _wrapper(site, *args, **kwargs):
            """Wrapper function to init and connect to site"""
            frappe.init(site=site)
            frappe.connect()
            try:
                return func(site=site, *args, **kwargs)
            finally:
                frappe.destroy()
        return _wrapper
    return decorator

@create_command('start-rag-consumer', help_text='Start the RAG Service consumer')
def start_consumer(site):
    """Start the RAG Service RabbitMQ consumer"""
    try:
        from rag_service.rag_service.utils.rabbitmq_consumer import RabbitMQConsumer
        click.echo(f"Starting RAG consumer for site: {site}")
        consumer = RabbitMQConsumer()
        consumer.start_consuming()
    except Exception as e:
        click.echo(f"Error starting consumer: {str(e)}")

commands = [
    start_consumer
]
