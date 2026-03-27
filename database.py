from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

DATABASE_URL = "sqlite:///./messenger.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(100))
    avatar_color = Column(String(7), default="#6366f1")
    created_at = Column(DateTime, default=datetime.utcnow)
    is_online = Column(Boolean, default=False)

    memberships = relationship("ChatMember", back_populates="user")
    messages = relationship("Message", back_populates="sender")


class Chat(Base):
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100))
    is_group = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))

    members = relationship("ChatMember", back_populates="chat")
    messages = relationship("Message", back_populates="chat", order_by="Message.created_at")


class ChatMember(Base):
    __tablename__ = "chat_members"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)

    chat = relationship("Chat", back_populates="members")
    user = relationship("User", back_populates="memberships")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text)
    message_type = Column(String(20), default="text")  # text, image, file, voice, video_circle
    file_path = Column(String(500))
    file_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Новые поля
    reply_to_id = Column(Integer, ForeignKey("messages.id"), nullable=True)
    is_edited = Column(Boolean, default=False)
    is_deleted = Column(Boolean, default=False)
    reactions = Column(Text, default="{}")  # JSON: {"👍": [user_id, ...], ...}

    chat = relationship("Chat", back_populates="messages")
    sender = relationship("User", back_populates="messages")
    reply_to = relationship("Message", remote_side="Message.id", foreign_keys=[reply_to_id])


class DeviceToken(Base):
    __tablename__ = 'device_tokens'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    token = Column(String, nullable=False, unique=True)
    platform = Column(String, default='android')  # 'android' | 'ios' | 'web'
    created_at = Column(DateTime, default=func.now())


def create_tables():
    Base.metadata.create_all(bind=engine)

    # Миграция: добавляем новые колонки если их нет (для существующих БД)
    from sqlalchemy import text, inspect
    inspector = inspect(engine)
    existing = [c['name'] for c in inspector.get_columns('messages')]

    with engine.connect() as conn:
        if 'reply_to_id' not in existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN reply_to_id INTEGER"))
        if 'is_edited' not in existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN is_edited BOOLEAN DEFAULT 0"))
        if 'is_deleted' not in existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
        if 'reactions' not in existing:
            conn.execute(text("ALTER TABLE messages ADD COLUMN reactions TEXT DEFAULT '{}'"))
        conn.commit()

    # Миграция: создаём таблицу device_tokens если её нет
    existing_tables = inspector.get_table_names()
    if 'device_tokens' not in existing_tables:
        DeviceToken.__table__.create(bind=engine, checkfirst=True)
