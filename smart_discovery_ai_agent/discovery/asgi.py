"""ASGI entry point:  uvicorn discovery.asgi:app"""
from .api import create_app

app = create_app()
