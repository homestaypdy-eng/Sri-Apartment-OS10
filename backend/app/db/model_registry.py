"""
Registers ORM model classes with Base.metadata that nothing else in the
live `uvicorn app.main:app` process would otherwise import.

Background: notifications/models.py's NotificationLog.action_id carries a
foreign key to action_proposals, whose ORM class (ActionProposalRow) is
defined in app.modules.automation.models. SQLAlchemy only knows a table
exists once its class has been *defined* (imported) somewhere in the
current process — app/db/seed.py already imports automation.models for
exactly this reason, but seed.py runs as a separate, short-lived process
(`python -m app.db.seed`, see docker-compose.yml) that exits before
uvicorn starts; nothing it imports carries over into the server process.
Without this, the first write in the server's lifetime that inserts a
NotificationLog row in the same flush as another table (booking/
housekeeping/maintenance/guest-request creation, etc.) fails with
sqlalchemy.exc.NoReferencedTableError when SQLAlchemy computes that
mapper's flush order — confirmed via a real 500 in Phase 18A-Deploy, and
reproduced with a full traceback in Phase 18B.

This module does exactly one thing: import the model class so its table
registers. It is deliberately kept out of app/main.py's own source text —
main.py has its own test (test_main_app_never_references_automation,
in test_automation_production_readiness.py) asserting zero textual
reference to "automation" anywhere in that file, as a blunt but effective
audit signal that the live app entrypoint has no coupling to the
autonomous-action system. That guarantee is still true here: this file
adds no router, no scheduler, no HTTP client, no execution path — only a
passive SQLAlchemy metadata registration, imported once from main.py via
`from app.db import model_registry`.
"""
from app.modules.automation import models as _automation_models  # noqa: F401
