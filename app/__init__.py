"""
SunAid-LoadPulse Python Package Initialization
Exposes the Flask app instance for WSGI servers like Gunicorn.
"""

from .app import app

__all__ = ["app"]
