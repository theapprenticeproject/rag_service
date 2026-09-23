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


@click.command('run-nightly-batch')
@click.option('--page-size', default=100, help='Feedback Requests pulled per page')
@click.option('--concurrency', default=8, help='How many to grade in parallel')
@click.option('--max-items', default=0, help='Optional cap for a smoke test (0 = all)')
@pass_context
def run_nightly_batch_command(context, page_size, concurrency, max_items):
    """Grade all Pending image/text submissions in parallel (overnight batch)."""
    site = context.sites[0]
    frappe.init(site=site)
    frappe.connect()

    try:
        # relative import: commands/ and core/ are siblings, so this is layout-independent
        from ..core.batch_runner import run_nightly_batch
        summary = run_nightly_batch(
            page_size=page_size, concurrency=concurrency, max_items=max_items
        )
        click.echo(f"Nightly batch done: {summary}")
    except Exception as e:
        click.echo(f"Error running nightly batch: {str(e)}")
    finally:
        frappe.destroy()


commands = [
    start_consumer,
    run_nightly_batch_command,
]
