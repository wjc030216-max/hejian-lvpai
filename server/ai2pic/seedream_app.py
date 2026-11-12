"""Canonical Seedream 5.0 Pro image API process."""
import sys
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

# Load the project-level .env before importing the provider.  The provider
# snapshots endpoint/model defaults at import time.
from common.runtime import load_project_env
load_project_env()
from common.seedream_service import create_app
from common import service_log

app = create_app("seedream_image")
service_log.install("seedream")

if __name__ == "__main__":
    from common.service_app import serve
    serve(app,10008)
