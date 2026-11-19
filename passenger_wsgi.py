import os
import sys

# Add the application directory to the Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load .env configuration if present
try:
    from dotenv import load_dotenv
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
except Exception:
    pass

# Import Flask app as 'application' for Phusion Passenger
from app import app as application
