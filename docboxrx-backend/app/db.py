"""Database module for persistent storage using PostgreSQL or SQLite."""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Check if we have a PostgreSQL DATABASE_URL
# Fallback to Neon Postgres if DATABASE_URL is not set in environment
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://neondb_owner:npg_Z60uvbwqlBzk@ep-mute-hill-adb7l32q-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require")
USE_POSTGRES = DATABASE_URL.startswith("postgres")

if USE_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
else:
    import sqlite3

# Use /data directory on Fly.io for persistent storage, or local file in backend dir for dev
_backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_default_sqlite_path = os.path.join(_backend_root, "docboxrx.db")
DB_PATH = os.environ.get("DATABASE_PATH", "/data/docboxrx.db" if os.path.exists("/data") else _default_sqlite_path)

# Global connection pool for Postgres to avoid repeated connection overhead
_pg_pool = None
_sqlite_conn = None

def _get_pg_pool():
    """Get or create the Postgres connection pool."""
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row}
        )
    return _pg_pool

def get_connection():
    """Get a database connection."""
    global _sqlite_conn
    if USE_POSTGRES:
        # Return a connection from the pool
        return _get_pg_pool().getconn()
    else:
        if _sqlite_conn is None:
            _sqlite_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _sqlite_conn.row_factory = sqlite3.Row
        return _sqlite_conn

def release_connection(conn):
    """Release a connection back to the pool (Postgres only)."""
    if USE_POSTGRES and _pg_pool is not None:
        _pg_pool.putconn(conn)

def p(query):
    """Convert SQLite ? placeholders to PostgreSQL %s placeholders."""
    if USE_POSTGRES:
        return query.replace('?', '%s')
    return query


def get_state_vectors(owner_id: str | None = None) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    if owner_id:
        cursor.execute(
            p(
                '''
                SELECT * FROM message_state_vectors
                WHERE lifecycle_state IN ('NEEDS_REPLY', 'WAITING')
                AND (current_owner_role = ? OR grant_id = ?)
                ORDER BY created_at DESC
                '''
            ),
            (owner_id, owner_id),
        )
    else:
        cursor.execute(
            p(
                '''
                SELECT * FROM message_state_vectors
                WHERE lifecycle_state IN ('NEEDS_REPLY', 'WAITING')
                ORDER BY created_at DESC
                '''
            )
        )
    rows = cursor.fetchall()
    release_connection(conn)
    return [dict(row) for row in rows]


def get_state_vector_by_id(vector_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM message_state_vectors WHERE id = ?'), (vector_id,))
    row = cursor.fetchone()
    release_connection(conn)
    return dict(row) if row else None


def update_state_vector_escalate(vector_id: str, owner_role: str = "lead_doctor") -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        p(
            '''
            UPDATE message_state_vectors
            SET lifecycle_state = ?, current_owner_role = ?, updated_at = ?
            WHERE id = ?
            '''
        ),
        ("OVERDUE", owner_role, datetime.utcnow().isoformat(), vector_id),
    )
    conn.commit()
    release_connection(conn)
    return get_state_vector_by_id(vector_id)


def create_message_event(event: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        p('''
            INSERT INTO message_events (id, vector_id, event_type, description, created_at)
            VALUES (?, ?, ?, ?, ?)
        '''),
        (
            event["id"],
            event["vector_id"],
            event["event_type"],
            event.get("description"),
            event["created_at"],
        ),
    )
    conn.commit()
    release_connection(conn)

def init_db():
    """Initialize the database tables."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            practice_name TEXT,
            hashed_password TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    
    # Messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            sender_domain TEXT NOT NULL,
            subject TEXT NOT NULL,
            snippet TEXT,
            zone TEXT NOT NULL,
            confidence REAL NOT NULL,
            reason TEXT NOT NULL,
            jone5_message TEXT NOT NULL,
            received_at TEXT NOT NULL,
            classified_at TEXT NOT NULL,
            corrected INTEGER DEFAULT 0,
            corrected_at TEXT,
            source_id TEXT,
            source_name TEXT,
            summary TEXT,
            recommended_action TEXT,
            action_type TEXT,
            draft_reply TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Add agent columns if they don't exist (for existing databases)
    # Use PostgreSQL-compatible syntax with IF NOT EXISTS
    if USE_POSTGRES:
        cursor.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS summary TEXT')
        cursor.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS recommended_action TEXT')
        cursor.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS action_type TEXT')
        cursor.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS draft_reply TEXT')
        # Add workflow state columns for Action Center
        cursor.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS status TEXT DEFAULT \'active\'')
        cursor.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS snoozed_until TEXT')
        cursor.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS needs_reply INTEGER DEFAULT 0')
        cursor.execute('ALTER TABLE messages ADD COLUMN IF NOT EXISTS replied_at TEXT')
    else:
        # SQLite doesn't support IF NOT EXISTS for columns, use try/except
        try:
            cursor.execute('ALTER TABLE messages ADD COLUMN summary TEXT')
        except:
            pass
        try:
            cursor.execute('ALTER TABLE messages ADD COLUMN recommended_action TEXT')
        except:
            pass
        try:
            cursor.execute('ALTER TABLE messages ADD COLUMN action_type TEXT')
        except:
            pass
        try:
            cursor.execute('ALTER TABLE messages ADD COLUMN draft_reply TEXT')
        except:
            pass
        # Add workflow state columns for Action Center
        try:
            cursor.execute('ALTER TABLE messages ADD COLUMN status TEXT DEFAULT \'active\'')
        except:
            pass
        try:
            cursor.execute('ALTER TABLE messages ADD COLUMN snoozed_until TEXT')
        except:
            pass
        try:
            cursor.execute('ALTER TABLE messages ADD COLUMN needs_reply INTEGER DEFAULT 0')
        except:
            pass
        try:
            cursor.execute('ALTER TABLE messages ADD COLUMN replied_at TEXT')
        except:
            pass
    
    # Sources table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            inbound_token TEXT UNIQUE NOT NULL,
            inbound_address TEXT NOT NULL,
            created_at TEXT NOT NULL,
            email_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Corrections table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS corrections (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            old_zone TEXT NOT NULL,
            new_zone TEXT NOT NULL,
            sender TEXT NOT NULL,
            corrected_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Rule overrides table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rule_overrides (
            sender_key TEXT PRIMARY KEY,
            zone TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    
    # CloudMailin messages table (for public endpoint)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cloudmailin_messages (
            id TEXT PRIMARY KEY,
            user_id TEXT DEFAULT 'cloudmailin-default-user',
            sender TEXT NOT NULL,
            sender_domain TEXT NOT NULL,
            subject TEXT NOT NULL,
            snippet TEXT,
            zone TEXT NOT NULL,
            confidence REAL NOT NULL,
            reason TEXT NOT NULL,
            jone5_message TEXT NOT NULL,
            received_at TEXT NOT NULL,
            classified_at TEXT NOT NULL,
            corrected INTEGER DEFAULT 0,
            source_id TEXT DEFAULT 'cloudmailin',
            source_name TEXT DEFAULT 'CloudMailin'
        )
    ''')
    
    # Nylas grants table (stores connected email accounts via Nylas)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS nylas_grants (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            grant_id TEXT NOT NULL,
            email TEXT NOT NULL,
            provider TEXT,
            created_at TEXT NOT NULL,
            last_sync_at TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Add token columns for existing databases
    if USE_POSTGRES:
        cursor.execute('ALTER TABLE nylas_grants ADD COLUMN IF NOT EXISTS access_token TEXT')
        cursor.execute('ALTER TABLE nylas_grants ADD COLUMN IF NOT EXISTS refresh_token TEXT')
        cursor.execute('ALTER TABLE nylas_grants ADD COLUMN IF NOT EXISTS expires_at TEXT')
        cursor.execute('ALTER TABLE nylas_grants ADD COLUMN IF NOT EXISTS updated_at TEXT')
    else:
        for column in ("access_token", "refresh_token", "expires_at", "updated_at"):
            try:
                cursor.execute(f'ALTER TABLE nylas_grants ADD COLUMN {column} TEXT')
            except:
                pass
    
    # Create indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sources_user_id ON sources(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sources_inbound_token ON sources(inbound_token)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nylas_grants_user_id ON nylas_grants(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_nylas_grants_grant_id ON nylas_grants(grant_id)')
    
    conn.commit()
    release_connection(conn)
    if USE_POSTGRES:
        print("PostgreSQL database initialized")
    else:
        print(f"SQLite database initialized at {DB_PATH}")

# User operations
def create_user(user_id: str, email: str, name: str, practice_name: str, hashed_password: str) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    created_at = datetime.utcnow().isoformat()
    cursor.execute(
        p('INSERT INTO users (id, email, name, practice_name, hashed_password, created_at) VALUES (?, ?, ?, ?, ?, ?)'),
        (user_id, email, name, practice_name, hashed_password, created_at)
    )
    conn.commit()
    release_connection(conn)
    return {"id": user_id, "email": email, "name": name, "practice_name": practice_name, "hashed_password": hashed_password, "created_at": created_at}

def get_user_by_id(user_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM users WHERE id = ?'), (user_id,))
    row = cursor.fetchone()
    release_connection(conn)
    return dict(row) if row else None

def get_user_by_email(email: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM users WHERE email = ?'), (email,))
    row = cursor.fetchone()
    release_connection(conn)
    return dict(row) if row else None

def email_exists(email: str) -> bool:
    return get_user_by_email(email) is not None

# Message operations
def create_message(message: dict) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('''
        INSERT INTO messages (id, user_id, sender, sender_domain, subject, snippet, zone, confidence, reason, jone5_message, received_at, classified_at, corrected, source_id, source_name, summary, recommended_action, action_type, draft_reply)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''), (
        message['id'], message['user_id'], message['sender'], message['sender_domain'],
        message['subject'], message.get('snippet'), message['zone'], message['confidence'],
        message['reason'], message['jone5_message'], message['received_at'], message['classified_at'],
        int(message.get('corrected', False)), message.get('source_id'), message.get('source_name'),
        message.get('summary'), message.get('recommended_action'), message.get('action_type'), message.get('draft_reply')
    ))
    conn.commit()
    release_connection(conn)
    return message

def get_messages_by_user(user_id: str, zone: str = None) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    if zone:
        cursor.execute(p('SELECT * FROM messages WHERE user_id = ? AND zone = ? ORDER BY received_at DESC'), (user_id, zone))
    else:
        cursor.execute(p('SELECT * FROM messages WHERE user_id = ? ORDER BY received_at DESC'), (user_id,))
    rows = cursor.fetchall()
    release_connection(conn)
    return [dict(row) for row in rows]

def get_message_by_id(message_id: str, user_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM messages WHERE id = ? AND user_id = ?'), (message_id, user_id))
    row = cursor.fetchone()
    release_connection(conn)
    return dict(row) if row else None

def update_message_zone(message_id: str, new_zone: str, corrected_at: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('UPDATE messages SET zone = ?, corrected = 1, corrected_at = ? WHERE id = ?'), (new_zone, corrected_at, message_id))
    conn.commit()
    release_connection(conn)

def delete_message(message_id: str, user_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('DELETE FROM messages WHERE id = ? AND user_id = ?'), (message_id, user_id))
    deleted = cursor.rowcount > 0
    conn.commit()
    release_connection(conn)
    return deleted

# Source operations
def create_source(source: dict) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('''
        INSERT INTO sources (id, user_id, name, inbound_token, inbound_address, created_at, email_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    '''), (
        source['id'], source['user_id'], source['name'], source['inbound_token'],
        source['inbound_address'], source['created_at'], source.get('email_count', 0)
    ))
    conn.commit()
    release_connection(conn)
    return source

def get_sources_by_user(user_id: str) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM sources WHERE user_id = ? ORDER BY created_at DESC'), (user_id,))
    rows = cursor.fetchall()
    release_connection(conn)
    return [dict(row) for row in rows]

def get_source_by_token(inbound_token: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM sources WHERE inbound_token = ?'), (inbound_token,))
    row = cursor.fetchone()
    release_connection(conn)
    return dict(row) if row else None

def delete_source(source_id: str, user_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('DELETE FROM sources WHERE id = ? AND user_id = ?'), (source_id, user_id))
    deleted = cursor.rowcount > 0
    conn.commit()
    release_connection(conn)
    return deleted

def increment_source_email_count(source_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('UPDATE sources SET email_count = email_count + 1 WHERE id = ?'), (source_id,))
    conn.commit()
    release_connection(conn)

# Correction operations
def create_correction(correction: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('''
        INSERT INTO corrections (id, user_id, old_zone, new_zone, sender, corrected_at)
        VALUES (?, ?, ?, ?, ?, ?)
    '''), (
        correction['id'], correction['user_id'], correction['old_zone'],
        correction['new_zone'], correction['sender'], correction['corrected_at']
    ))
    conn.commit()
    release_connection(conn)

def get_corrections_by_user(user_id: str) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM corrections WHERE user_id = ? ORDER BY corrected_at DESC'), (user_id,))
    rows = cursor.fetchall()
    release_connection(conn)
    return [dict(row) for row in rows]

# Rule override operations
def set_rule_override(sender_key: str, zone: str):
    conn = get_connection()
    cursor = conn.cursor()
    if USE_POSTGRES:
        cursor.execute('''
            INSERT INTO rule_overrides (sender_key, zone, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (sender_key) DO UPDATE SET zone = EXCLUDED.zone, created_at = EXCLUDED.created_at
        ''', (sender_key, zone, datetime.utcnow().isoformat()))
    else:
        cursor.execute('''
            INSERT OR REPLACE INTO rule_overrides (sender_key, zone, created_at)
            VALUES (?, ?, ?)
        ''', (sender_key, zone, datetime.utcnow().isoformat()))
    conn.commit()
    release_connection(conn)

def get_rule_override(sender_key: str) -> str | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT zone FROM rule_overrides WHERE sender_key = ?'), (sender_key,))
    row = cursor.fetchone()
    release_connection(conn)
    if row:
        return row['zone'] if isinstance(row, dict) else row[0]
    return None

# CloudMailin message operations
def create_cloudmailin_message(message: dict) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('''
        INSERT INTO cloudmailin_messages (id, user_id, sender, sender_domain, subject, snippet, zone, confidence, reason, jone5_message, received_at, classified_at, corrected, source_id, source_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''), (
        message['id'], message.get('user_id', 'cloudmailin-default-user'), message['sender'], message['sender_domain'],
        message['subject'], message.get('snippet'), message['zone'], message['confidence'],
        message['reason'], message['jone5_message'], message['received_at'], message['classified_at'],
        int(message.get('corrected', False)), message.get('source_id', 'cloudmailin'), message.get('source_name', 'CloudMailin')
    ))
    conn.commit()
    release_connection(conn)
    return message

def get_cloudmailin_messages() -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM cloudmailin_messages ORDER BY received_at DESC')
    rows = cursor.fetchall()
    release_connection(conn)
    return [dict(row) for row in rows]

# Nylas grant operations
def create_nylas_grant(grant: dict) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('''
        INSERT INTO nylas_grants (id, user_id, grant_id, email, provider, created_at, last_sync_at, access_token, refresh_token, expires_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''), (
        grant['id'], grant['user_id'], grant['grant_id'], grant['email'],
        grant.get('provider'), grant['created_at'], grant.get('last_sync_at'),
        grant.get('access_token'), grant.get('refresh_token'), grant.get('expires_at'), grant.get('updated_at')
    ))
    conn.commit()
    release_connection(conn)
    return grant

def get_nylas_grants_by_user(user_id: str) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM nylas_grants WHERE user_id = ? ORDER BY created_at DESC'), (user_id,))
    rows = cursor.fetchall()
    release_connection(conn)
    return [dict(row) for row in rows]

def get_nylas_grant_by_grant_id(grant_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('SELECT * FROM nylas_grants WHERE grant_id = ?'), (grant_id,))
    row = cursor.fetchone()
    release_connection(conn)
    return dict(row) if row else None

def update_nylas_grant_sync_time(grant_id: str, last_sync_at: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('UPDATE nylas_grants SET last_sync_at = ? WHERE grant_id = ?'), (last_sync_at, grant_id))
    conn.commit()
    release_connection(conn)


def update_nylas_grant_tokens(grant_id: str, access_token: str = None, refresh_token: str = None, expires_at: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        p('UPDATE nylas_grants SET access_token = ?, refresh_token = ?, expires_at = ?, updated_at = ? WHERE grant_id = ?'),
        (access_token, refresh_token, expires_at, datetime.utcnow().isoformat(), grant_id),
    )
    conn.commit()
    release_connection(conn)

def delete_nylas_grant(grant_id: str, user_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('DELETE FROM nylas_grants WHERE grant_id = ? AND user_id = ?'), (grant_id, user_id))
    deleted = cursor.rowcount > 0
    conn.commit()
    release_connection(conn)
    return deleted

# Message status operations for Action Center
def update_message_status(message_id: str, user_id: str, status: str, snoozed_until: str = None) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    if snoozed_until:
        cursor.execute(p('UPDATE messages SET status = ?, snoozed_until = ? WHERE id = ? AND user_id = ?'), (status, snoozed_until, message_id, user_id))
    else:
        cursor.execute(p('UPDATE messages SET status = ?, snoozed_until = NULL WHERE id = ? AND user_id = ?'), (status, message_id, user_id))
    updated = cursor.rowcount > 0
    conn.commit()
    release_connection(conn)
    return updated

def mark_message_replied(message_id: str, user_id: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(p('UPDATE messages SET needs_reply = 0, replied_at = ? WHERE id = ? AND user_id = ?'), (datetime.utcnow().isoformat(), message_id, user_id))
    updated = cursor.rowcount > 0
    conn.commit()
    release_connection(conn)
    return updated

def get_action_items(user_id: str) -> dict:
    """Get action items for the Action Center / Daily Brief."""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    
    # Get active messages that need action (STAT and TODAY zones, not done/archived)
    cursor.execute(p('''
        SELECT * FROM messages 
        WHERE user_id = ? AND (status IS NULL OR status = 'active') 
        AND zone IN ('STAT', 'TODAY')
        ORDER BY 
            CASE zone WHEN 'STAT' THEN 1 WHEN 'TODAY' THEN 2 ELSE 3 END,
            received_at DESC
    '''), (user_id,))
    urgent_items = [dict(row) for row in cursor.fetchall()]
    
    # Get messages needing reply (action_type = 'reply' and not replied)
    cursor.execute(p('''
        SELECT * FROM messages 
        WHERE user_id = ? AND (status IS NULL OR status = 'active')
        AND action_type = 'reply' AND (replied_at IS NULL)
        ORDER BY received_at DESC
    '''), (user_id,))
    needs_reply = [dict(row) for row in cursor.fetchall()]
    
    # Get snoozed messages that are now due
    cursor.execute(p('''
        SELECT * FROM messages 
        WHERE user_id = ? AND status = 'snoozed' AND snoozed_until <= ?
        ORDER BY snoozed_until ASC
    '''), (user_id, now))
    snoozed_due = [dict(row) for row in cursor.fetchall()]
    
    # Get recently completed items (last 24 hours)
    yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat()
    cursor.execute(p('''
        SELECT COUNT(*) as count FROM messages 
        WHERE user_id = ? AND status = 'done' AND classified_at >= ?
    '''), (user_id, yesterday))
    done_today = cursor.fetchone()
    done_count = done_today['count'] if isinstance(done_today, dict) else done_today[0]
    
    release_connection(conn)
    
    return {
        'urgent_items': urgent_items,
        'needs_reply': needs_reply,
        'snoozed_due': snoozed_due,
        'done_today': done_count,
        'total_action_items': len(urgent_items) + len(snoozed_due)
    }

# Initialize database on import
init_db()
