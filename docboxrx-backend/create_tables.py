import sqlite3
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./docboxrx.db")

# Extract the path from the URL
if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL[10:]  # Remove 'sqlite:///'
else:
    db_path = "docboxrx.db"

# The SQL Schema for SQLite
MIGRATION_SQL = """
-- 1. The State Vector Table
CREATE TABLE IF NOT EXISTS message_state_vectors (
    id TEXT PRIMARY KEY,
    nylas_message_id TEXT UNIQUE NOT NULL,
    grant_id TEXT NOT NULL,
    
    -- The Vector (AI Analysis)
    intent_label TEXT NOT NULL,
    risk_score REAL NOT NULL,
    context_blob TEXT DEFAULT '{}',
    summary TEXT,
    
    -- The Routing
    current_owner_role TEXT,
    deadline_at TEXT,
    
    -- The Lifecycle
    lifecycle_state TEXT DEFAULT 'NEW',
    is_overdue INTEGER DEFAULT 0,
    
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 2. Indexes for Speed
CREATE INDEX IF NOT EXISTS idx_vectors_lifecycle ON message_state_vectors(lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_vectors_risk ON message_state_vectors(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_vectors_deadline ON message_state_vectors(deadline_at);

-- 3. The Event Log
CREATE TABLE IF NOT EXISTS message_events (
    id TEXT PRIMARY KEY,
    vector_id TEXT REFERENCES message_state_vectors(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    description TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

def run_migration():
    print(f"Connecting to SQLite Database at {db_path}...")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        print("Connected. Running Migration...")
        
        cursor.executescript(MIGRATION_SQL)
        conn.commit()
        
        print("Migration Complete! Tables 'message_state_vectors' and 'message_events' created.")
        conn.close()
        
    except Exception as e:
        print(f"Migration Failed: {e}")

if __name__ == "__main__":
    run_migration()