# 3. Django + HTMX for the UI, not Streamlit

Date: 2026-08-01

## Status
Accepted

## Context
The v1 UI needs to be genuinely interactive (sliders/checkboxes that
recompute a chart) and stay pure Python. Streamlit was the initial
plan: fastest to a working interactive demo, zero separate frontend
code. Reconsidered before building anything.

## Decision
Use Django, with HTMX for the interactive re-computation (a form
POSTs on change, a view returns a re-rendered HTML fragment, HTMX
swaps it in) and Plotly's `fig.to_html()` for charts. No hand-written
JavaScript.

## Consequences
- Streamlit's session model doesn't extend cleanly to a real
  multi-user hosted service -- it would have meant a full UI rewrite
  when this project outgrows a single local user. Django's local
  (`runserver` + SQLite) and hosted (gunicorn + Postgres + Celery)
  modes are the same codebase.
- Django's ORM gives free persistence for `ScenarioRecord` and
  `BacktestRunRecord` -- comparing today's run against last month's
  is a query, not a feature to build.
- Cost: more boilerplate than Streamlit for a single interactive
  view (a form, a view, two templates, vs. one script). Acceptable
  given the above.
- HTMX swap granularity (whole `#results` div) is coarse for now --
  fine for a single chart, revisit if the UI grows more panels that
  shouldn't all re-render together.
