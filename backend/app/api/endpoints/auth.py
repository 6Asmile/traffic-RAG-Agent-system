# app/api/endpoints/auth.py
import random
import string

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.user import User
from app.schemas.user import UserAuth, Token, UserOut
from app.core import security

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/register", response_model=UserOut)
def register(user_in: UserAuth, db: Session = Depends(get_db)):
    """注册接口"""
    # 1. 检查用户是否存在
    user = db.query(User).filter(User.username == user_in.username).first()
    if user:
        raise HTTPException(status_code=400, detail="用户名已被占用")

    # 2. 加密密码并保存
    new_user = User(
        username=user_in.username,
        hashed_password=security.get_password_hash(user_in.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=Token)
def login(user_in: UserAuth, db: Session = Depends(get_db)):
    """登录接口"""
    # 1. 查找用户
    user = db.query(User).filter(User.username == user_in.username).first()
    if not user or not security.verify_password(user_in.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="用户名或密码错误")

    # 2. 生成 Token
    access_token = security.create_access_token(subject=user.id)
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/captcha")
def get_captcha(session_id: str):
    """生成一个简单的 4 位验证码并存入 Redis"""
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    # 存入 Redis，5 分钟过期 (假设你已经有了 redis_client)
    # redis_client.setex(f"captcha:{session_id}", 300, code.lower())
    return {"captcha_code": code} # 实际项目中应该返回图片流，这里为了方便萌新先返回文本