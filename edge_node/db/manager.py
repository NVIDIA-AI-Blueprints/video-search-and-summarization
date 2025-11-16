import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy import create_engine, text, Engine, select, update
from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Boolean, func

logger = logging.getLogger(__name__)

# --- SQLAlchemy Base and Models ---

class Base(DeclarativeBase):
    pass

class Event(Base):
    __tablename__ = "events"
    
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    json: Mapped[str] = mapped_column(String) # Full event JSON
    status: Mapped[str] = mapped_column(String) # e.g., 'PENDING_UPLOAD', 'UPLOADED', 'FAILED'
    created_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self) -> Dict[str, Any]:
        data = json.loads(self.json)
        data['status'] = self.status
        data['created_at'] = self.created_at.isoformat() if self.created_at else None
        data['updated_at'] = self.updated_at.isoformat() if self.updated_at else None
        return data

class PendingUpload(Base):
    __tablename__ = "pending_uploads"
    
    upload_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String)
    filepath: Mapped[str] = mapped_column(String)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_ts: Mapped[Optional[DateTime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String) # e.g., 'PENDING', 'PROCESSING', 'FAILED', 'COMPLETE'
    checksum: Mapped[Optional[str]] = mapped_column(String)
    final_url: Mapped[Optional[str]] = mapped_column(String)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "event_id": self.event_id,
            "filepath": self.filepath,
            "attempts": self.attempts,
            "status": self.status,
            "checksum": self.checksum,
            "final_url": self.final_url,
            "last_attempt_ts": self.last_attempt_ts.isoformat() if self.last_attempt_ts else None,
        }

class KBMeta(Base):
    __tablename__ = "kb_meta"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_version: Mapped[str] = mapped_column(String)
    applied_at: Mapped[DateTime] = mapped_column(DateTime, default=func.now())

class DeviceState(Base):
    __tablename__ = "device_state"
    
    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_heartbeat: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
    versions: Mapped[str] = mapped_column(String) # JSON blob of service versions

# --- Database Manager ---

class DBManager:
    """
    Manages the SQLite database connection and provides helper methods
    for data access and migrations.
    """
    
    def __init__(self, db_path: Path, schema_path: Path):
        self.db_path = db_path
        self.schema_path = schema_path
        self.engine: Engine = create_engine(f"sqlite:///{self.db_path}")
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def initialize_db(self):
        """
        Creates the database file and applies the schema if it doesn't exist.
        This acts as a simple migration manager.
        """
        if not self.db_path.exists():
            logger.info(f"Database file not found at {self.db_path}. Creating new database.")
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create tables using SQLAlchemy models
            Base.metadata.create_all(bind=self.engine)
            logger.info("SQLAlchemy models created successfully.")
            
            # Apply additional schema from the SQL file (e.g., triggers, indexes)
            try:
                with open(self.schema_path, 'r') as f:
                    sql_script = f.read()
                
                with self.engine.connect() as connection:
                    # SQLite dialect requires executing multiple statements separately
                    for statement in sql_script.split(';'):
                        statement = statement.strip()
                        if statement:
                            connection.execute(text(statement))
                    connection.commit()
                logger.info(f"Additional schema from {self.schema_path} applied.")
            except Exception as e:
                logger.error(f"Failed to apply additional schema: {e}")
                # Optionally, roll back or exit if schema application is critical
                
        else:
            logger.info(f"Database already exists at {self.db_path}. Skipping creation.")

    def get_session(self):
        """Dependency for getting a database session."""
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # --- Helper Methods for Aggregator/Uploader ---

    def get_pending_uploads(self, limit: int = 10) -> List[PendingUpload]:
        """Retrieves a list of PENDING uploads."""
        with self.SessionLocal() as session:
            stmt = select(PendingUpload).where(PendingUpload.status == "PENDING_UPLOAD").limit(limit)
            # Fetch all attributes to prevent lazy-loading errors outside the session
            uploads = list(session.scalars(stmt).all())
            # Detach objects from the session before returning
            for upload in uploads:
                session.expunge(upload)
            return uploads

    def insert_event(self, event_data: Dict[str, Any], local_clip_path: str) -> Tuple[str, str]:
        """Inserts a new event and a corresponding pending upload entry."""
        event_id = event_data['event_id']
        upload_id = f"upload-{event_id}" # Simple upload ID for now
        
        with self.SessionLocal() as session:
            # 1. Insert into events table
            event = Event(
                event_id=event_id,
                json=json.dumps(event_data),
                status="PENDING_UPLOAD"
            )
            session.add(event)
            
            # 2. Insert into pending_uploads table
            upload = PendingUpload(
                upload_id=upload_id,
                event_id=event_id,
                filepath=local_clip_path,
                status="PENDING_UPLOAD"
            )
            session.add(upload)
            
            session.commit()
            logger.info(f"Event {event_id} and upload {upload_id} inserted as PENDING_UPLOAD.")
            return event_id, upload_id

    def update_upload_status(self, upload_id: str, status: str, **kwargs):
        """Updates the status and other fields of a pending upload."""
        with self.SessionLocal() as session:
            stmt = update(PendingUpload).where(PendingUpload.upload_id == upload_id).values(
                status=status,
                last_attempt_ts=func.now(),
                **kwargs
            )
            session.execute(stmt)
            
            # Also update the main event status if it's a final state
            if status in ["UPLOADED", "FAILED"]:
                upload = session.get(PendingUpload, upload_id)
                if upload:
                    event_stmt = update(Event).where(Event.event_id == upload.event_id).values(
                        status=status
                    )
                    session.execute(event_stmt)
            
            session.commit()
            logger.info(f"Upload {upload_id} status updated to {status}.")

# --- Main Entry Point for Testing/CLI ---

if __name__ == "__main__":
    # Example usage for testing the manager
    DB_PATH = Path("/tmp/vss_events.db")
    SCHEMA_PATH = Path(__file__).parent / "schema.sql"
    
    # Clean up old DB for fresh test
    if DB_PATH.exists():
        DB_PATH.unlink()
        
    manager = DBManager(DB_PATH, SCHEMA_PATH)
    manager.initialize_db()
    
    # Test insertion
    test_event_data = {
      "tenant_id": "acme",
      "device_id": "thor-mini-001",
      "camera_id": "cam-01",
      "event_id": "evt-20251116-0001",
      "timestamp": "2025-11-16T10:02:30Z",
      "event_type": "motion",
      "objects":[],
      "dense_caption":"A test event",
      "audio_text": None,
      "local_clip_path": "/var/lib/vss/clips/...",
      "confidence":0.9
    }
    
    manager.insert_event(test_event_data, "/tmp/test_clip.mp4")
    
    # Test retrieval
    pending = manager.get_pending_uploads()
    print("\n--- Pending Uploads ---")
    for upload in pending:
        print(upload.to_dict())
        
    # Test status update
    manager.update_upload_status(pending[0].upload_id, "PROCESSING", attempts=1)
    
    print("\n--- After Update ---")
    with manager.SessionLocal() as session:
        updated_upload = session.get(PendingUpload, pending[0].upload_id)
        print(updated_upload.to_dict())
        
        updated_event = session.get(Event, pending[0].event_id)
        print(updated_event.to_dict())
