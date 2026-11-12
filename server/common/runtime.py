"""Runtime bootstrap shared by provider services.

The production/test hosts keep secrets in the project-level ``.env`` file.
Gunicorn does not source that file automatically, so provider modules must
load it before importing code that reads environment variables at module load
time.  Existing process environment variables always win over values in the
file, which keeps container/systemd deployments configurable.
"""
from __future__ import annotations

import os
from dotenv import load_dotenv


def load_project_env() -> str | None:
    """Load the nearest project ``.env`` and return its path, if found."""
    # Containers receive an explicit environment; never discover a phase-one .env.
    selected=os.getenv('PHASE2_ENV_FILE')
    if selected:
        load_dotenv(selected,override=False)
        return selected
    return None



def require_env(*names: str) -> str:
    """Return the first configured variable or raise a clear startup error."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise RuntimeError(f"缺少必需环境变量: {' / '.join(names)}")

