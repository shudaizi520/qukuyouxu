"""HTTP routes for frequent status summaries and on-demand details."""
from .status_summary import build_status_details, build_status_summary


def attach_status_routes(app, store, engine, runtime) -> None:
    @app.get("/api/status")
    def status():
        return build_status_summary(app, store, engine, runtime)

    @app.get("/api/status/details")
    def status_details():
        return build_status_details(store)
