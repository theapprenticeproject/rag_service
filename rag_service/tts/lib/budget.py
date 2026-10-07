from dataclasses import dataclass

import frappe

from ..constants import ACTIVE_STATUSES, RUN_DOCTYPE, STATUS_COMPLETED, STATUS_COMPLETED_WITH_FAILURES

MAX_ACTIVE_RUNS_COUNTED = 1


@dataclass(frozen=True)
class BudgetEstimate:
    seconds: float
    cost_usd: float


@dataclass(frozen=True)
class BudgetCheck:
    fits: bool
    trimmed_count: int
    estimate: BudgetEstimate
    today_spend_usd: float
    month_spend_usd: float
    reason: str


def _recent_rate(settings, sample_size=5):
    rows = frappe.get_all(
        RUN_DOCTYPE,
        filters={
            "status": ["in", [STATUS_COMPLETED, STATUS_COMPLETED_WITH_FAILURES]],
            "written": [">", 0],
        },
        fields=["pod_run_seconds", "written", "pod_startup_seconds"],
        order_by="creation desc",
        limit_page_length=sample_size,
    )
    if not rows:
        return settings.est_startup_seconds, settings.est_seconds_per_clip
    total_startup = 0.0
    total_marginal = 0.0
    n = 0
    for row in rows:
        written = row.written or 0
        if written <= 0:
            continue
        startup = row.pod_startup_seconds or settings.est_startup_seconds
        run_seconds = row.pod_run_seconds or 0
        marginal = run_seconds / float(written)
        total_startup += startup
        total_marginal += marginal
        n += 1
    if n == 0:
        return settings.est_startup_seconds, settings.est_seconds_per_clip
    return total_startup / n, total_marginal / n


def _estimate_from_rate(settings, item_count, rate, startup, per_clip):
    raw_seconds = (startup + item_count * per_clip) * settings.estimate_safety_factor
    cost = raw_seconds / 3600.0 * rate
    return BudgetEstimate(seconds=raw_seconds, cost_usd=cost)


def estimate_cost(settings, item_count, hourly_rate=None):
    rate = hourly_rate if hourly_rate is not None else settings.fallback_hourly_rate_usd
    startup, per_clip = _recent_rate(settings)
    return _estimate_from_rate(settings, item_count, rate, startup, per_clip)


def _day_bounds():
    now = frappe.utils.now_datetime()
    start = frappe.utils.get_datetime(frappe.utils.nowdate())
    return start, now


def _month_bounds():
    now = frappe.utils.now_datetime()
    start = frappe.utils.get_first_day(now)
    return frappe.utils.get_datetime(start), now


def _active_run_contribution(settings, active_count):
    counted = min(active_count, MAX_ACTIVE_RUNS_COUNTED)
    return counted * settings.max_cost_per_run_usd


def _sum_spend(start, end, settings):
    rows = frappe.get_all(
        RUN_DOCTYPE,
        filters={"creation": ["between", [start, end]]},
        fields=["status", "estimated_cost_usd_final", "estimated_cost_usd"],
    )
    finished_total = 0.0
    active_count = 0
    for row in rows:
        if row.status in ACTIVE_STATUSES:
            active_count += 1
        else:
            finished_total += row.estimated_cost_usd_final or row.estimated_cost_usd or 0.0
    return finished_total + _active_run_contribution(settings, active_count)


def today_spend(settings):
    start, end = _day_bounds()
    return _sum_spend(start, end, settings)


def month_spend(settings):
    start, end = _month_bounds()
    return _sum_spend(start, end, settings)


def fit_to_caps(settings, items, hourly_rate=None):
    today = today_spend(settings)
    month = month_spend(settings)

    day_room = max(0.0, settings.max_cost_per_day_usd - today)
    month_room = max(0.0, settings.max_cost_per_month_usd - month)
    room = min(day_room, month_room, settings.max_cost_per_run_usd)

    rate = hourly_rate if hourly_rate is not None else settings.fallback_hourly_rate_usd
    startup, per_clip = _recent_rate(settings)

    count = min(len(items), settings.max_clips_per_run)
    if count == 0:
        return BudgetCheck(
            fits=False, trimmed_count=0,
            estimate=BudgetEstimate(seconds=0.0, cost_usd=0.0),
            today_spend_usd=today, month_spend_usd=month, reason="no_items",
        )

    estimate = _estimate_from_rate(settings, count, rate, startup, per_clip)
    if estimate.cost_usd <= room:
        return BudgetCheck(
            fits=True, trimmed_count=count, estimate=estimate,
            today_spend_usd=today, month_spend_usd=month, reason="",
        )

    lo, hi = 0, count
    while lo < hi:
        mid = (lo + hi + 1) // 2
        trial = _estimate_from_rate(settings, mid, rate, startup, per_clip)
        if trial.cost_usd <= room:
            lo = mid
        else:
            hi = mid - 1
    trimmed = lo
    final_estimate = _estimate_from_rate(settings, trimmed, rate, startup, per_clip)
    return BudgetCheck(
        fits=trimmed > 0, trimmed_count=trimmed, estimate=final_estimate,
        today_spend_usd=today, month_spend_usd=month,
        reason="" if trimmed > 0 else "budget_exhausted",
    )
