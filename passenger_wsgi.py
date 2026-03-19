"""
Passenger WSGI entry point for cPanel hosting.
"""
import os
import sys

# Add the app directory to path
app_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, app_dir)

from app import app as application

# Start background scheduler
try:
    from scheduler import start_scheduler_thread
    start_scheduler_thread()
except Exception as e:
    print(f"[WARNING] Scheduler failed to start: {e}")
