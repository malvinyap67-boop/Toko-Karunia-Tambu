# WSGI configuration for PythonAnywhere.
# Adjust the path below to match where you upload app.py.
import os
import sys

# This should point to the directory that contains app.py and the rest of the project.
# On PythonAnywhere the usual location is /home/<your_username>/toko
project_home = '/home/your_username/toko'

sys.path.insert(0, project_home)
os.environ.setdefault('SECRET_KEY', '')

from app import app as application
