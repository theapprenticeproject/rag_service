frappe.listview_settings['Feedback Request'] = {
    add_fields: ["result_status", "is_plagiarized", "is_ai_generated"],

    get_indicator: function(doc) {
        const status_map = {
            "Pending": ["orange", "Pending"],
            "Success - Original": ["green", "Original"],
            "Success - Flagged": ["red", "Flagged"],
            "Failed": ["darkgrey", "Failed"]
        };

        const [color, label] = status_map[doc.result_status] || ["grey", "Unknown"];
        return [__(label), color, `result_status,=,${doc.result_status}`];
    },

    formatters: {
        result_status: function(value) {
            const badges = {
                "Pending": '<span class="badge badge-warning">Pending</span>',
                "Success - Original": '<span class="badge badge-success">✓ Original</span>',
                "Success - Flagged": '<span class="badge badge-danger">⚠ Flagged</span>',
                "Failed": '<span class="badge badge-secondary">✗ Failed</span>'
            };
            return badges[value] || value;
        }
    }
};
