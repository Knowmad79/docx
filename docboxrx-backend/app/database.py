from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./docboxrx.db")

# SQLite requires this specific argument to work with FastAPI
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
    engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    
    id = Column(String, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    practice_name = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="user", cascade="all, delete-orphan")
    corrections = relationship("Correction", back_populates="user", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    sender = Column(String, nullable=False)
    sender_domain = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    snippet = Column(Text, nullable=True)
    zone = Column(String, nullable=False)  # STAT, TODAY, THIS_WEEK, LATER
    confidence = Column(Float, nullable=False)
    reason = Column(String, nullable=False)
    jone5_message = Column(String, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow)
    classified_at = Column(DateTime, default=datetime.utcnow)
    corrected = Column(Boolean, default=False)
    corrected_at = Column(DateTime, nullable=True)
    source_id = Column(String, nullable=True)
    source_name = Column(String, nullable=True)
    
    user = relationship("User", back_populates="messages")

class Source(Base):
    __tablename__ = "sources"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    inbound_token = Column(String, unique=True, nullable=False, index=True)
    inbound_address = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    email_count = Column(Integer, default=0)
    
    user = relationship("User", back_populates="sources")

class Correction(Base):
    __tablename__ = "corrections"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    old_zone = Column(String, nullable=False)
    new_zone = Column(String, nullable=False)
    sender = Column(String, nullable=False)
    corrected_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="corrections")

class RuleOverride(Base):
    __tablename__ = "rule_overrides"
    
    id = Column(String, primary_key=True)
    sender_key = Column(String, unique=True, nullable=False, index=True)  # e.g., "sender:email@example.com"
    zone = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# CloudMailin messages (for the public endpoint)
class CloudMailinMessage(Base):
    __tablename__ = "cloudmailin_messages"
    
    id = Column(String, primary_key=True)
    user_id = Column(String, default="cloudmailin-default-user")
    sender = Column(String, nullable=False)
    sender_domain = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    snippet = Column(Text, nullable=True)
    zone = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    reason = Column(String, nullable=False)
    jone5_message = Column(String, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow)
    classified_at = Column(DateTime, default=datetime.utcnow)
    corrected = Column(Boolean, default=False)
    source_id = Column(String, default="cloudmailin")
    source_name = Column(String, default="CloudMailin")

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
