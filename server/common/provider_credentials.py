"""Credentials never fall back across services or accounts."""
import os

def credential(name, *unused_legacy_names):
    return os.getenv(name, '').strip()
