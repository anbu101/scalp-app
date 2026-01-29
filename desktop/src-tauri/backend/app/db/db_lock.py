import threading

# 🔒 SINGLE SQLite writer lock
DB_LOCK = threading.Lock()
