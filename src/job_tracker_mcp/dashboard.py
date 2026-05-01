"""Prefab dashboard: vibrant metrics, filter bar, interactive DataTable actions."""

from __future__ import annotations

from typing import Any

from prefab_ui.actions.mcp import CallTool, SendMessage
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Button,
    ButtonGroup,
    Card,
    CardContent,
    Column,
    DataTable,
    DataTableColumn,
    ExpandableRow,
    Heading,
    Metric,
    Row,
    Text,
)


def _tier_badge(days_display: str, tier: str) -> str:
    if tier == "red":
        return f"🔴 {days_display}"
    if tier == "amber":
        return f"🟠 {days_display}"
    if tier == "neutral":
        return f"🟢 {days_display}"
    return days_display


def build_dashboard_app(
    default_filter: str,
    table_jobs: list[dict[str, Any]],
    metrics: dict[str, int],
) -> PrefabApp:
    """
    table_jobs: flattened rows for DataTable + optional stale_tier, company, title, id, etc.
    metrics keys: total, applied, screen, interview, offer, rejected, withdrawn, stale_red
    """
    rows_out: list[Any] = []
    for j in table_jobs:
        c = str(j.get("company", ""))
        t = str(j.get("title", ""))
        jid = str(j.get("id", ""))
        st = str(j.get("status", ""))
        fit = j.get("fit_percent")
        fit_s = "—" if fit is None else f"{fit}%"
        d = j.get("days_since_applied")
        tier = str(j.get("stale_tier") or "unknown")
        if d is None:
            days_part = "—"
        else:
            days_part = f"{int(d)}d"
        days_cell = _tier_badge(days_part, tier) if d is not None else "—"

        flat = {
            "company": c,
            "title": t,
            "status": st,
            "fit": fit_s,
            "days_ago": days_cell,
            "intel": str(j.get("intel_snippet") or "—")[:120],
        }

        follow_msg = (
            f"Draft a concise follow-up email for my application at {c} "
            f"for the role **{t}** (job id `{jid}`). "
            f"Keep it professional and under 120 words."
        )
        intel_msg = f"Refresh company intel for **{c}** and summarize any culture or news signals."

        with Row(gap=2, css_class="items-center flex-wrap") as action_row:
            Button("Follow up ↗", variant="secondary", size="sm", on_click=SendMessage(follow_msg))
            Button(
                "Intel ↗",
                variant="outline",
                size="sm",
                on_click=SendMessage(intel_msg),
            )
            Button(
                "Move to Interview ↗",
                variant="default",
                size="sm",
                on_click=SendMessage(
                    f"Update job id `{jid}` to status Interview and suggest two preparation priorities."
                ),
            )
        rows_out.append(ExpandableRow(flat, detail=action_row))

    filters = ["All", "Applied", "Screen", "Interview", "Offer", "Rejected", "Withdrawn"]

    with Column(gap=4, css_class="p-5 max-w-none") as view:
        Heading(f"Job tracker — filter: {default_filter}")
        Text(
            "Pipeline metrics and quick actions for next best move. "
            "Use status filters and row actions to continue in chat instantly.",
            css_class="text-muted-foreground text-sm font-medium",
        )

        with Row(gap=4, css_class="flex-wrap"):
            with Card():
                with CardContent(css_class="p-4"):
                    Metric(
                        label="Total",
                        value=str(metrics.get("total", 0)),
                        description="Rows in tracker",
                    )
            with Card():
                with CardContent(css_class="p-4"):
                    Metric(
                        label="In progress",
                        value=str(
                            metrics.get("applied", 0)
                            + metrics.get("screen", 0)
                            + metrics.get("interview", 0)
                        ),
                        description="Applied + Screen + Interview",
                    )
            with Card():
                with CardContent(css_class="p-4"):
                    Metric(
                        label="Offers",
                        value=str(metrics.get("offer", 0)),
                        description="Celebrate your wins",
                    )
            with Card():
                with CardContent(css_class="p-4"):
                    Metric(
                        label="Needs follow-up",
                        value=str(metrics.get("stale_red", 0)),
                        description="15+ days since applied",
                        trend_sentiment="negative",
                    )

        with Row(gap=2, css_class="items-center flex-wrap"):
            Text("Status filter:", css_class="text-sm font-medium")
            with ButtonGroup(css_class="flex-wrap"):
                for lab in filters:
                    variant = "default" if lab == default_filter else "outline"
                    Button(
                        lab,
                        variant=variant,
                        size="sm",
                        on_click=CallTool(
                            "push_dashboard",
                            arguments={"default_filter": lab},
                        ),
                    )

        DataTable(
            columns=[
                DataTableColumn(key="company", header="Company", sortable=True),
                DataTableColumn(key="title", header="Role", sortable=True, min_width="160px"),
                DataTableColumn(key="status", header="Status", sortable=True, width="110px"),
                DataTableColumn(key="fit", header="Resume %", width="80px"),
                DataTableColumn(key="days_ago", header="Days ago", width="120px"),
                DataTableColumn(key="intel", header="Intel (cached)", max_width="240px"),
            ],
            rows=rows_out,
            search=True,
            paginated=len(rows_out) > 10,
            page_size=10,
        )

    state = {
        "default_filter": default_filter,
        "metrics": metrics,
        "row_count": len(rows_out),
    }
    return PrefabApp(title="Job application dashboard", view=view, state=state)


def _compute_pipeline_metrics(jobs: list[dict[str, Any]], flags: dict[str, int]) -> dict[str, int]:
    def cnt(status: str) -> int:
        return sum(1 for j in jobs if str(j.get("status")) == status)

    return {
        "total": len(jobs),
        "applied": cnt("Applied"),
        "screen": cnt("Screen"),
        "interview": cnt("Interview"),
        "offer": cnt("Offer"),
        "rejected": cnt("Rejected"),
        "withdrawn": cnt("Withdrawn"),
        "stale_red": int(flags.get("stale_red", 0)),
    }


def build_prefab_dashboard(
    default_filter: str = "All",
    jobs: list[dict[str, Any]] | None = None,
) -> PrefabApp:
    """Same pipeline as MCP `push_dashboard`: deadlines merge, optional status filter, Prefab tree."""
    from job_tracker_mcp.deadlines import check_deadlines as compute_deadlines
    from job_tracker_mcp.storage import load_jobs_raw
    from job_tracker_mcp.user_fit import enrich_jobs_with_user_fit

    jobs_list = list(jobs) if jobs is not None else list(load_jobs_raw().get("jobs", []))
    dl = compute_deadlines(jobs_list)
    enriched_by_id = {str(j.get("id")): j for j in dl["jobs"]}
    merged: list[dict[str, Any]] = []
    for j in jobs_list:
        jid = str(j.get("id"))
        base = dict(j)
        base.update(enriched_by_id.get(jid, {}))
        merged.append(base)

    filt = (default_filter or "All").strip()
    if filt != "All":
        merged = [j for j in merged if str(j.get("status")) == filt]

    merged = enrich_jobs_with_user_fit(merged)

    metrics = _compute_pipeline_metrics(dl["jobs"], dl["flags"])
    return build_dashboard_app(filt, merged, metrics)
