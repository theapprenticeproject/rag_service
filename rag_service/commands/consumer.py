import click
import frappe
from frappe.commands import pass_context
from rag_service.rag_service.utils.rabbitmq_consumer import RabbitMQConsumer


@click.command('start-rag-consumer')
@pass_context
def start_consumer(context):
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
    site = context.sites[0]
    frappe.init(site=site)
    frappe.connect()
    try:
        from ..core.batch_runner import run_nightly_batch
        summary = run_nightly_batch(page_size=page_size, concurrency=concurrency, max_items=max_items)
        click.echo(f"Nightly batch done: {summary}")
    except Exception as e:
        click.echo(f"Error running nightly batch: {str(e)}")
    finally:
        frappe.destroy()


@click.command('start-tts-batch')
@click.option('--force', is_flag=True, help='Bypass the minimum-items threshold for this one run')
@pass_context
def start_tts_batch_command(context, force):
    site = context.sites[0]
    frappe.init(site=site)
    frappe.connect()
    try:
        from ..tts.job.deploy import deploy_new
        result = deploy_new(trigger_type="Manual", triggered_by="CLI", force=force)
        click.echo(f"TTS batch: {result}")
    except Exception as e:
        click.echo(f"Error running TTS batch: {str(e)}")
    finally:
        frappe.destroy()


@click.command('reap-tts-runs')
@pass_context
def reap_tts_runs_command(context):
    site = context.sites[0]
    frappe.init(site=site)
    frappe.connect()
    try:
        from ..tts.job.reaper import run_reaper_now
        result = run_reaper_now()
        click.echo(f"TTS reaper: {result}")
    finally:
        frappe.destroy()


@click.command('tts-preview')
@pass_context
def tts_preview_command(context):
    site = context.sites[0]
    frappe.init(site=site)
    frappe.connect()
    try:
        from ..tts.api.operator import preview_eligible
        frappe.set_user("Administrator")
        result = preview_eligible()
        click.echo(f"TTS preview: {result}")
    finally:
        frappe.destroy()


commands = [
    start_consumer,
    run_nightly_batch_command,
    start_tts_batch_command,
    reap_tts_runs_command,
    tts_preview_command,
]