import frappe


def billed_seconds(pod_created_at, confirmed_end_at):
    if not pod_created_at or not confirmed_end_at:
        return 0.0
    return max(0.0, frappe.utils.time_diff_in_seconds(confirmed_end_at, pod_created_at))


def cost_for_seconds(seconds, hourly_rate):
    return seconds / 3600.0 * hourly_rate


def per_clip_costs(cost_usd, written_count):
    if not written_count:
        return None, None
    per_clip = cost_usd / written_count
    per_1000 = per_clip * 1000.0
    return round(per_clip, 6), round(per_1000, 4)


def summarize(run, hourly_rate):
    billed = billed_seconds(run.get("pod_created_at"), run.get("terminated_at") or frappe.utils.now_datetime())
    cost = cost_for_seconds(billed, hourly_rate or 0.0)
    written = run.get("written") or 0
    per_clip, per_1000 = per_clip_costs(cost, written)
    return {
        "billed_seconds": round(billed, 2),
        "estimated_cost_usd_final": round(cost, 4),
        "cost_per_clip_usd": per_clip,
        "cost_per_1000_usd": per_1000,
    }
