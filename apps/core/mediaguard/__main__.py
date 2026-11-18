"""Start: python -m mediaguard  (rol via MG_ROLE)."""

import logging
import os

import uvicorn

from mediaguard.app import create_app
from mediaguard.bootstrap import load_secret_files


class _QuietAccess(logging.Filter):
    """Toegangslog alleen voor fouten (>=400) en wijzigingen; health-checks en polls van
    de UI (tientallen per minuut) vervuilen anders het TrueNAS-log."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 5:
            method, status = str(args[1]), args[4]
            try:
                return int(status) >= 400 or method not in ("GET", "HEAD")
            except (TypeError, ValueError):
                return True
        return True


if __name__ == "__main__":
    from mediaguard import logs

    # Geheimen lezen mag vóór de schemacontrole. Logbestanden pas daarna openen.
    load_secret_files()
    application = create_app()
    logs.configure(os.environ.get("MG_ROLE", "core"),
                   None if application.state.ctx.db.read_only else logs.log_dir(),
                   os.environ.get("MG_LOG_LEVEL", "info"))
    logging.getLogger("uvicorn.access").addFilter(_QuietAccess())
    # proxy_headers uit: core bepaalt zelf welke proxy-headers te vertrouwen zijn
    # (mediaguard.security.net). Uvicorn zou X-Forwarded-For anders blind overnemen.
    # log_config=None: uvicorn richt zelf niets in en logt via de root-logger hierboven
    uvicorn.run(application, host=os.environ.get("MG_BIND", "0.0.0.0"),
                port=int(os.environ.get("MG_PORT", "8100")), proxy_headers=False,
                log_config=None)
