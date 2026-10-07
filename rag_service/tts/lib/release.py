import asyncio

import frappe


def release_stuck_rows(run_name, target_status):
    from rag_service.core.feedback_service import FeedbackService

    stuck = frappe.get_all(
        "Feedback Request",
        filters={"audio_run": run_name, "audio_status": "Processing"},
        pluck="name",
    )
    fs = FeedbackService()
    for fr_name in stuck:
        frappe.db.set_value("Feedback Request", fr_name, {
            "audio_status": "Failed",
            "audio_error": f"run_{target_status.lower().replace(' ', '_')}",
        }, update_modified=False)
        frappe.db.commit()
        asyncio.run(fs.release_to_lms_if_ready(fr_name))
    return len(stuck)