frappe.ui.form.on("TTS Settings", {
    refresh(frm) {
        frm.add_custom_button(__("Check Setup"), () => {
            frappe.call({
                method: "rag_service.tts.api.operator.check_setup",
                callback: (r) => {
                    if (!r.message) return;
                    const rows = Object.entries(r.message)
                        .filter(([k]) => k !== "all_ok")
                        .map(([k, v]) => {
                            const ok = v && v.ok;
                            return `<tr><td>${ok ? "✅" : "❌"} ${k}</td><td>${v.error || v.value || ""}</td></tr>`;
                        }).join("");
                    frappe.msgprint({
                        title: r.message.all_ok ? __("Setup OK") : __("Setup Issues Found"),
                        message: `<table class="table table-bordered"><tbody>${rows}</tbody></table>`,
                        indicator: r.message.all_ok ? "green" : "red",
                    });
                },
            });
        });
    },
});
