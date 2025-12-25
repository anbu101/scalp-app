from fastapi import APIRouter
from app.brokers.zerodha.auth import get_login_url, generate_session
from app.brokers.zerodha.session import save_token, is_logged_in

router = APIRouter()

@router.get("/auth/zerodha/login")
def login():
    return {"url": get_login_url()}

@router.get("/auth/zerodha/callback")
def callback(request_token: str):
    data = generate_session(request_token)
    save_token(data["access_token"])
    return {"status": "ok"}

@router.get("/auth/status")
def status():
    return {"zerodha": is_logged_in()}
