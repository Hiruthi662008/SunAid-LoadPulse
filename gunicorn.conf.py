import os

# Automatically bind to Render's dynamic PORT environment variable
port = os.environ.get("PORT", "5000")
bind = f"0.0.0.0:{port}"
workers = 2
threads = 4
timeout = 120
accesslog = "-"
errorlog = "-"
