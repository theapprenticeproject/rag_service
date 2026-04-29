# Copyright (c) 2024, TAP and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class PromptTemplate(Document):
    def validate(self):
        expected_segment_types = {
            "system_segment": "system",
            "grading_segment": "grading",
            "subject_segment": "subject",
            "output_segment": "output",
        }

        for fieldname, expected_type in expected_segment_types.items():
            segment_name = self.get(fieldname)
            if not segment_name:
                continue

            segment_type = frappe.db.get_value("Prompt Segment", segment_name, "segment_type")
            if segment_type != expected_type:
                frappe.throw(
                    _("{0} must reference a Prompt Segment of type '{1}'").format(
                        self.meta.get_label(fieldname), expected_type
                    )
                )
