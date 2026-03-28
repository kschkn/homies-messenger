import json
import os
import uuid
import hashlib
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db, create_tables, User, Chat, ChatMember, Message, DeviceToken

import logging
logger = logging.getLogger(__name__)

# ─── Firebase Init (optional) ────────────────────────────────
firebase_enabled = False
try:
    import firebase_admin
    from firebase_admin import credentials, messaging

    if os.path.exists("firebase-credentials.json"):
        cred = credentials.Certificate("firebase-credentials.json")
        firebase_admin.initialize_app(cred)
        firebase_enabled = True
        logger.info("Firebase initialized from firebase-credentials.json")
    elif os.environ.get("FIREBASE_CREDENTIALS"):
        import tempfile
        cred_json = os.environ["FIREBASE_CREDENTIALS"]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(cred_json)
            tmp_path = f.name
        cred = credentials.Certificate(tmp_path)
        firebase_admin.initialize_app(cred)
        firebase_enabled = True
        logger.info("Firebase initialized from FIREBASE_CREDENTIALS env var")
    else:
        logger.warning("Firebase credentials not found. Push notifications disabled.")
except ImportError:
    logger.warning("firebase-admin not installed. Push notifications disabled.")

app = FastAPI(title="Home Messenger")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── WebSocket Manager ────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        if user_id not in self.active:
            self.active[user_id] = []
        self.active[user_id].append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int):
        if user_id in self.active:
            try:
                self.active[user_id].remove(websocket)
            except ValueError:
                pass
            if not self.active[user_id]:
                del self.active[user_id]

    async def send_to_user(self, user_id: int, data: dict):
        if user_id in self.active:
            message = json.dumps(data, ensure_ascii=False, default=str)
            dead = []
            for ws in self.active[user_id]:
                try:
                    await ws.send_text(message)
                except:
                    dead.append(ws)
            for ws in dead:
                try:
                    self.active[user_id].remove(ws)
                except ValueError:
                    pass

    async def broadcast_to_chat(self, chat_id: int, data: dict, db: Session):
        members = db.query(ChatMember).filter(ChatMember.chat_id == chat_id).all()
        for member in members:
            await self.send_to_user(member.user_id, data)

    def online_users(self) -> list[int]:
        return list(self.active.keys())


manager = ConnectionManager()


# ─── Helpers ──────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_user_by_token(token: str, db: Session) -> Optional[User]:
    try:
        user_id = int(token.split(":")[0])
        return db.query(User).filter(User.id == user_id).first()
    except:
        return None


def message_to_dict(msg: Message) -> dict:
    # Реакции
    try:
        reactions = json.loads(msg.reactions or "{}")
    except:
        reactions = {}

    # Reply
    reply_data = None
    if msg.reply_to_id and msg.reply_to:
        r = msg.reply_to
        reply_data = {
            "id": r.id,
            "sender_name": r.sender.display_name or r.sender.username if r.sender else "?",
            "content": r.content if r.message_type == "text" else f"[{r.message_type}]",
            "type": r.message_type,
        }

    return {
        "id": msg.id,
        "chat_id": msg.chat_id,
        "sender_id": msg.sender_id,
        "sender_name": msg.sender.display_name or msg.sender.username if msg.sender else "?",
        "sender_color": msg.sender.avatar_color if msg.sender else "#6366f1",
        "content": msg.content,
        "type": msg.message_type,
        "file_path": msg.file_path,
        "file_name": msg.file_name,
        "created_at": msg.created_at.isoformat(),
        "reply_to": reply_data,
        "is_edited": bool(msg.is_edited),
        "is_deleted": bool(msg.is_deleted),
        "reactions": reactions,
    }


# ─── Schemas ──────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateChatRequest(BaseModel):
    name: Optional[str] = None
    member_ids: list[int]
    is_group: bool = False


class DeviceTokenRequest(BaseModel):
    fcm_token: str
    platform: str = "android"


class DeleteDeviceTokenRequest(BaseModel):
    fcm_token: str


# ─── Auth ─────────────────────────────────────────────────────

@app.post("/api/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    import random
    colors = ["#6366f1", "#ec4899", "#14b8a6", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6"]
    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        display_name=req.display_name or req.username,
        avatar_color=random.choice(colors),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = f"{user.id}:{user.username}"
    return {"token": token, "user": {"id": user.id, "username": user.username, "display_name": user.display_name, "avatar_color": user.avatar_color}}


@app.post("/api/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or user.password_hash != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = f"{user.id}:{user.username}"
    return {"token": token, "user": {"id": user.id, "username": user.username, "display_name": user.display_name, "avatar_color": user.avatar_color}}


# ─── Users ────────────────────────────────────────────────────

@app.get("/api/users")
def get_users(token: str, db: Session = Depends(get_db)):
    me = get_user_by_token(token, db)
    if not me:
        raise HTTPException(status_code=401)
    users = db.query(User).filter(User.id != me.id).all()
    online = manager.online_users()
    return [{"id": u.id, "username": u.username, "display_name": u.display_name or u.username, "avatar_color": u.avatar_color, "is_online": u.id in online} for u in users]


# ─── Chats ────────────────────────────────────────────────────

@app.get("/api/chats")
def get_chats(token: str, db: Session = Depends(get_db)):
    me = get_user_by_token(token, db)
    if not me:
        raise HTTPException(status_code=401)
    memberships = db.query(ChatMember).filter(ChatMember.user_id == me.id).all()
    result = []
    for m in memberships:
        chat = m.chat
        visible = [msg for msg in chat.messages if not msg.is_deleted]
        last_msg = visible[-1] if visible else None
        name = chat.name
        other_color = "#6366f1"
        if not chat.is_group:
            other = next((cm.user for cm in chat.members if cm.user_id != me.id), None)
            if other:
                name = other.display_name or other.username
                other_color = other.avatar_color
        preview = None
        if last_msg:
            if last_msg.message_type == "text":
                preview = last_msg.content
            elif last_msg.message_type == "voice":
                preview = "🎤 Голосовое"
            elif last_msg.message_type == "video_circle":
                preview = "⭕ Видео"
            elif last_msg.message_type == "image":
                preview = "🖼 Фото"
            else:
                preview = "📎 Файл"
        result.append({
            "id": chat.id,
            "name": name,
            "is_group": chat.is_group,
            "avatar_color": other_color if not chat.is_group else "#6366f1",
            "last_message": preview,
            "last_time": last_msg.created_at.isoformat() if last_msg else None,
        })
    result.sort(key=lambda x: x["last_time"] or "", reverse=True)
    return result


@app.post("/api/chats")
def create_chat(req: CreateChatRequest, token: str, db: Session = Depends(get_db)):
    me = get_user_by_token(token, db)
    if not me:
        raise HTTPException(status_code=401)
    if not req.is_group and len(req.member_ids) == 1:
        other_id = req.member_ids[0]
        existing = db.query(Chat).join(ChatMember, Chat.id == ChatMember.chat_id).filter(Chat.is_group == False, ChatMember.user_id == me.id).all()
        for ch in existing:
            member_ids = {cm.user_id for cm in ch.members}
            if member_ids == {me.id, other_id}:
                return {"id": ch.id}
    chat = Chat(name=req.name, is_group=req.is_group, created_by=me.id)
    db.add(chat)
    db.flush()
    for uid in list(set([me.id] + req.member_ids)):
        db.add(ChatMember(chat_id=chat.id, user_id=uid))
    db.commit()
    return {"id": chat.id}


@app.get("/api/chats/{chat_id}/messages")
def get_messages(chat_id: int, token: str, db: Session = Depends(get_db)):
    me = get_user_by_token(token, db)
    if not me:
        raise HTTPException(status_code=401)
    member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == me.id).first()
    if not member:
        raise HTTPException(status_code=403)
    messages = db.query(Message).filter(Message.chat_id == chat_id).order_by(Message.created_at).all()
    return [message_to_dict(m) for m in messages]


# ─── Upload ───────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(
    token: str = Form(...),
    chat_id: int = Form(...),
    file: UploadFile = File(...),
    is_voice: Optional[str] = Form(None),
    is_video_circle: Optional[str] = Form(None),
    reply_to_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    me = get_user_by_token(token, db)
    if not me:
        raise HTTPException(status_code=401)

    ext = os.path.splitext(file.filename)[1] if file.filename else ""
    unique_name = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)

    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    if is_video_circle == "true":
        msg_type = "video_circle"
    elif is_voice == "true" or (file.filename and file.filename.startswith("voice.")):
        msg_type = "voice"
    elif file.content_type and file.content_type.startswith("image/"):
        msg_type = "image"
    else:
        msg_type = "file"

    msg = Message(
        chat_id=chat_id,
        sender_id=me.id,
        content=None,
        message_type=msg_type,
        file_path=f"/uploads/{unique_name}",
        file_name=file.filename,
        reply_to_id=reply_to_id,
        reactions="{}",
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    await manager.broadcast_to_chat(chat_id, {"type": "new_message", "message": message_to_dict(msg)}, db)
    send_push_notification(chat_id, me.id, me.display_name or me.username, None, msg_type, db)
    return {"ok": True, "message": message_to_dict(msg)}


# ─── Device Token Endpoints ───────────────────────────────────

@app.post("/api/device-token")
def register_device_token(req: DeviceTokenRequest, token: str, db: Session = Depends(get_db)):
    me = get_user_by_token(token, db)
    if not me:
        raise HTTPException(status_code=401)
    existing = db.query(DeviceToken).filter(DeviceToken.token == req.fcm_token).first()
    if existing:
        existing.user_id = me.id
        existing.platform = req.platform
    else:
        db.add(DeviceToken(user_id=me.id, token=req.fcm_token, platform=req.platform))
    db.commit()
    return {"ok": True}


@app.delete("/api/device-token")
def delete_device_token(req: DeleteDeviceTokenRequest, token: str, db: Session = Depends(get_db)):
    me = get_user_by_token(token, db)
    if not me:
        raise HTTPException(status_code=401)
    db.query(DeviceToken).filter(DeviceToken.token == req.fcm_token).delete()
    db.commit()
    return {"ok": True}


# ─── Push Notifications ──────────────────────────────────────

def send_push_notification(chat_id: int, sender_id: int, sender_name: str, content: str, message_type: str, db: Session):
    if not firebase_enabled:
        return

    # Определяем превью контента
    type_previews = {
        "image": "Фото",
        "voice": "Голосовое",
        "video_circle": "Видео",
        "file": "Файл",
    }
    body = type_previews.get(message_type, content or "")

    # Находим всех участников чата кроме отправителя
    members = db.query(ChatMember).filter(
        ChatMember.chat_id == chat_id,
        ChatMember.user_id != sender_id
    ).all()

    for member in members:
        # Отправляем push только если пользователь не имеет активного WebSocket
        if member.user_id in manager.active and manager.active[member.user_id]:
            continue

        # Получаем все токены устройств этого пользователя
        tokens = db.query(DeviceToken).filter(DeviceToken.user_id == member.user_id).all()
        for dt in tokens:
            try:
                msg = messaging.Message(
                    notification=messaging.Notification(
                        title=sender_name,
                        body=body,
                    ),
                    data={
                        'chat_id': str(chat_id),
                        'sender_id': str(sender_id),
                        'type': 'new_message'
                    },
                    token=dt.token
                )
                messaging.send(msg)
            except Exception as e:
                error_str = str(e).lower()
                if 'not-found' in error_str or 'invalid' in error_str or 'unregistered' in error_str:
                    db.query(DeviceToken).filter(DeviceToken.id == dt.id).delete()
                    db.commit()
                    logger.info(f"Removed invalid FCM token for user {member.user_id}")
                else:
                    logger.error(f"FCM send error: {e}")


# ─── WebSocket ────────────────────────────────────────────────

@app.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str, db: Session = Depends(get_db)):
    me = get_user_by_token(token, db)
    if not me:
        await websocket.close(code=4001)
        return

    await manager.connect(websocket, me.id)
    me.is_online = True
    db.commit()

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            # ── Отправка текста ──
            if data["type"] == "send_message":
                chat_id = data["chat_id"]
                content = data["content"]
                reply_to_id = data.get("reply_to_id")

                member = db.query(ChatMember).filter(ChatMember.chat_id == chat_id, ChatMember.user_id == me.id).first()
                if not member:
                    continue

                msg = Message(
                    chat_id=chat_id,
                    sender_id=me.id,
                    content=content,
                    message_type="text",
                    reply_to_id=reply_to_id,
                    reactions="{}",
                )
                db.add(msg)
                db.commit()
                db.refresh(msg)

                await manager.broadcast_to_chat(chat_id, {"type": "new_message", "message": message_to_dict(msg)}, db)
                send_push_notification(chat_id, me.id, me.display_name or me.username, content, "text", db)

            # ── Редактирование ──
            elif data["type"] == "edit_message":
                msg_id = data["message_id"]
                new_content = data["content"]
                msg = db.query(Message).filter(Message.id == msg_id, Message.sender_id == me.id).first()
                if msg and msg.message_type == "text" and not msg.is_deleted:
                    msg.content = new_content
                    msg.is_edited = True
                    db.commit()
                    await manager.broadcast_to_chat(msg.chat_id, {"type": "message_edited", "message": message_to_dict(msg)}, db)

            # ── Удаление ──
            elif data["type"] == "delete_message":
                msg_id = data["message_id"]
                msg = db.query(Message).filter(Message.id == msg_id, Message.sender_id == me.id).first()
                if msg and not msg.is_deleted:
                    msg.is_deleted = True
                    msg.content = None
                    db.commit()
                    await manager.broadcast_to_chat(msg.chat_id, {"type": "message_deleted", "message_id": msg_id, "chat_id": msg.chat_id}, db)

            # ── Реакции ──
            elif data["type"] == "react":
                msg_id = data["message_id"]
                emoji = data["emoji"]
                msg = db.query(Message).filter(Message.id == msg_id).first()
                if msg and not msg.is_deleted:
                    # Проверяем что юзер в чате
                    member = db.query(ChatMember).filter(ChatMember.chat_id == msg.chat_id, ChatMember.user_id == me.id).first()
                    if not member:
                        continue
                    try:
                        reactions = json.loads(msg.reactions or "{}")
                    except:
                        reactions = {}
                    if emoji not in reactions:
                        reactions[emoji] = []
                    if me.id in reactions[emoji]:
                        reactions[emoji].remove(me.id)  # снять реакцию
                        if not reactions[emoji]:
                            del reactions[emoji]
                    else:
                        reactions[emoji].append(me.id)  # поставить
                    msg.reactions = json.dumps(reactions)
                    db.commit()
                    await manager.broadcast_to_chat(msg.chat_id, {"type": "reaction_updated", "message_id": msg_id, "reactions": reactions}, db)

    except WebSocketDisconnect:
        manager.disconnect(websocket, me.id)
        me.is_online = False
        db.commit()


# ─── Frontend ─────────────────────────────────────────────────

@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")


@app.on_event("startup")
def startup():
    create_tables()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
