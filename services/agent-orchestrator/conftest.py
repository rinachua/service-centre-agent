import os
import tempfile
from pathlib import Path

# Set default environment variables for testing before any imports of app.main
if "AUDIT_DB_PATH" not in os.environ:
    os.environ["AUDIT_DB_PATH"] = str(Path(tempfile.gettempdir()) / "agent-orchestrator-test-audit.db")
