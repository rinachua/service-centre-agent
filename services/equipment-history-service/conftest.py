import os
import tempfile
from pathlib import Path

# Set default environment variables for testing before any imports of app.main
if "DB_PATH" not in os.environ:
    os.environ["DB_PATH"] = str(Path(tempfile.gettempdir()) / "equipment-history-service-test.db")

if "SEED_PATH" not in os.environ:
    # Go up from services/equipment-history-service to services, then to root, then to data/seed
    os.environ["SEED_PATH"] = str(Path(__file__).parent.parent.parent / "data" / "seed")
