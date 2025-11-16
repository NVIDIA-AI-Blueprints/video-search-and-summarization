-- edge_node/db/schema.sql

-- Table for storing event metadata
CREATE TABLE events (
    event_id TEXT PRIMARY KEY NOT NULL,
    json TEXT NOT NULL, -- Full event JSON
    status TEXT NOT NULL, -- e.g., 'PENDING_UPLOAD', 'UPLOADED', 'FAILED'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table for managing the upload process for clips/files associated with an event
CREATE TABLE pending_uploads (
    upload_id TEXT PRIMARY KEY NOT NULL,
    event_id TEXT NOT NULL,
    filepath TEXT NOT NULL, -- Local path to the clip/file
    attempts INTEGER DEFAULT 0,
    last_attempt_ts TIMESTAMP,
    status TEXT NOT NULL, -- e.g., 'PENDING', 'PROCESSING', 'FAILED', 'COMPLETE'
    checksum TEXT, -- SHA256 checksum of the file
    final_url TEXT, -- URL of the file after successful upload
    FOREIGN KEY (event_id) REFERENCES events (event_id)
);

-- Table for tracking knowledge base version
CREATE TABLE kb_meta (
    id INTEGER PRIMARY KEY,
    kb_version TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table for tracking device state and versions
CREATE TABLE device_state (
    device_id TEXT PRIMARY KEY NOT NULL,
    last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    versions TEXT -- JSON blob of service versions
);

-- Index for quick lookup of pending uploads
CREATE INDEX idx_pending_uploads_status ON pending_uploads (status);

-- Trigger to update the updated_at column on event modification
CREATE TRIGGER update_events_updated_at
AFTER UPDATE ON events
FOR EACH ROW
BEGIN
    UPDATE events SET updated_at = CURRENT_TIMESTAMP WHERE event_id = NEW.event_id;
END;
