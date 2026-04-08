"""
Passenger WSGI entry point for cPanel hosting.
Keeps startup fast — no heavy imports at load time.
"""
import os
import sys

# Add the app directory to path
app_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, app_dir)

from app import app as application
