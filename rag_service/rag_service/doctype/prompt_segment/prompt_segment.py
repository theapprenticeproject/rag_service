# Copyright (c) 2026, TAP and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PromptSegment(Document):
    def validate(self):
        if not self.version:
            self.version = 1
