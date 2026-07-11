import os
import tempfile
from pathlib import Path

# Set default environment variables for testing before any imports of app.main
if "SEED_PATH" not in os.environ:
    # Go up from services/knowledge-service to services, then to root, then to data/seed
    os.environ["SEED_PATH"] = str(Path(__file__).parent.parent.parent / "data" / "seed")
