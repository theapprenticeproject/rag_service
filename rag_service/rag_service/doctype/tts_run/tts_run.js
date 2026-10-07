const TTS_RUN_ACTIVE_STATUSES = ["Deploying", "Starting", "Running", "Stopping"];

frappe.listview_settings['TTS Run'] = {
    add_fields: ["trigger_type", "triggered_by", "written", "pod_failed", "not_processed", "estimated_cost_usd_final"],

    get_indicator: function(doc) {
        if (TTS_RUN_ACTIVE_STATUSES.includes(doc.status)) {
            return [__(doc.status), "orange", `status,=,${doc.status}`];
        }
        if (doc.status === "Completed") {
            return [__(doc.status), "green", `status,=,${doc.status}`];
        }
        if (doc.status === "Completed With Failures") {
            return [__(doc.status), "yellow", `status,=,${doc.status}`];
        }
        if (["Failed", "Timed Out", "Lost", "Deploy Failed"].includes(doc.status)) {
            return [__(doc.status), "red", `status,=,${doc.status}`];
        }
        if (["Stopped", "Skipped"].includes(doc.status)) {
            return [__(doc.status), "grey", `status,=,${doc.status}`];
        }
        if (doc.status === "Draft") {
            return [__(doc.status), "light-grey", `status,=,${doc.status}`];
        }
        return [__(doc.status || "Unknown"), "grey", ""];
    },

    onload: function(listview) {
        listview.page.add_inner_button(__("Deploy Batch Now"), () => {
            frappe.db.count("TTS Run", {
                filters: { status: ["in", TTS_RUN_ACTIVE_STATUSES] },
            }).then((count) => {
                if (count > 0) {
                    frappe.msgprint(__("A run is already active"));
                    return;
                }

                frappe.call({
                    method: "rag_service.tts.api.operator.preview_eligible",
                    callback: (preview) => {
                        const msg = preview.message || {};
                        const dialog = new frappe.ui.Dialog({
                            title: __("Deploy TTS Batch"),
                            fields: [
                                {
                                    fieldtype: "HTML",
                                    fieldname: "preview_html",
                                    options: `<p>${__("Eligible")}: ${msg.eligible_found || 0}<br>` +
                                        `${__("Would deploy")}: ${msg.would_deploy_count || 0}<br>` +
                                        `${__("Estimated cost")}: $${(msg.estimated_cost_usd || 0).toFixed(2)}</p>`,
                                },
                                {
                                    fieldtype: "Check",
                                    fieldname: "force",
                                    label: __("Force start (bypass minimum items)"),
                                },
                            ],
                            primary_action_label: __("Deploy"),
                            primary_action: (values) => {
                                dialog.get_primary_btn().prop("disabled", true).addClass("btn-spinner");
                                frappe.call({
                                    method: "rag_service.tts.api.operator.start_batch_manual",
                                    args: { force: values.force ? 1 : 0 },
                                    callback: (r) => {
                                        dialog.hide();
                                        const res = r.message || {};
                                        if (res.deployed) {
                                            frappe.show_alert({
                                                message: __("Run {0} deployed", [res.run]),
                                                indicator: "green",
                                            });
                                            frappe.set_route("Form", "TTS Run", res.run);
                                        } else {
                                            frappe.msgprint(res.detail || res.status_reason || res.reason || __("Deploy was skipped"));
                                        }
                                    },
                                    error: () => {
                                        dialog.get_primary_btn().prop("disabled", false).removeClass("btn-spinner");
                                    },
                                });
                            },
                        });
                        dialog.show();
                    },
                });
            });
        });
    },
};

frappe.ui.form.on('TTS Run', {
    refresh(frm) {
        if (frm.is_new()) return;

        const group = __("Operator Actions");

        if (TTS_RUN_ACTIVE_STATUSES.includes(frm.doc.status)) {
            frm.add_custom_button(__("Stop Run"), () => {
                frappe.confirm(
                    __("This asks the pod to finish its current item and stop cleanly, which can take up to {0} minutes. Continue?", [frm.doc.heartbeat_timeout_minutes || "several"]),
                    () => {
                        frappe.call({
                            method: "rag_service.tts.api.operator.cancel_run",
                            args: { run_name: frm.doc.name },
                            callback: () => frm.reload_doc(),
                        });
                    }
                );
            }, group);
        }

        const terminationNotDone = !["Verified", "Not Needed"].includes(frm.doc.termination_status);
        if (terminationNotDone || TTS_RUN_ACTIVE_STATUSES.includes(frm.doc.status)) {
            const btn = frm.add_custom_button(__("Force Terminate"), () => {
                frappe.prompt(
                    {
                        fieldtype: "Data",
                        fieldname: "confirm_name",
                        label: __("Type the run name ({0}) to confirm", [frm.doc.name]),
                    },
                    (values) => {
                        if (values.confirm_name !== frm.doc.name) {
                            frappe.msgprint(__("Name did not match. Force Terminate cancelled."));
                            return;
                        }
                        frappe.call({
                            method: "rag_service.tts.api.operator.force_terminate",
                            args: { run_name: frm.doc.name },
                            callback: () => frm.reload_doc(),
                        });
                    },
                    __("Confirm Force Terminate")
                );
            }, group);
            btn.addClass("btn-danger");
        }
    },
});