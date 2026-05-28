# -*- coding: utf-8 -*-
import os
import re
import json
import shutil
import threading
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
from loguru import logger

from . import config
from .core import run_sign_in

PROJECT_ROOT = Path(os.getcwd()).absolute()

app = FastAPI(title="ChaoXing Sign-in Configuration Web Panel")
sign_in_lock = threading.Lock()

class BarkConfig(BaseModel):
    device_key: Optional[str] = ""
    device_token: Optional[str] = ""

class WeChatConfig(BaseModel):
    user_id: Optional[str] = ""

class AccountConfig(BaseModel):
    enable: bool = True
    username: str
    password: str
    host: str = "hbkjzy.qmx.chaoxing.com"
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0
    device: str = "iPhone 12"
    expires: Optional[str] = None
    enable_notification: bool = False
    notification_type: List[str] = []
    bark: Optional[BarkConfig] = None
    wechat: Optional[WeChatConfig] = None

class CreateAccountRequest(BaseModel):
    id: str
    config: AccountConfig

class LogCaptureSink:
    def __init__(self):
        self.logs = []
    def write(self, message):
        self.logs.append(message.strip())

def get_config_path(account_id: str) -> Path:
    if account_id == "main":
        return PROJECT_ROOT / "config.json"
    if not re.match(r"^[a-zA-Z0-9_\-]+$", account_id):
        raise HTTPException(status_code=400, detail="Invalid account ID")
    return PROJECT_ROOT / "configs" / account_id / "config.json"

@app.get("/", response_class=HTMLResponse)
async def get_index():
    template_file = PROJECT_ROOT / "templates" / "index.html"
    if not template_file.exists():
        return HTMLResponse("<h1>Templates/index.html not found. Make sure to create it.</h1>", status_code=404)
    with open(template_file, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/config.example.json")
async def get_config_example():
    example_file = PROJECT_ROOT / "config.example.json"
    if not example_file.exists():
        raise HTTPException(status_code=404, detail="config.example.json not found")
    with open(example_file, "r", encoding="utf-8") as f:
        return json.load(f)

@app.get("/api/accounts")
async def list_accounts():
    accounts = []
    main_path = get_config_path("main")
    if main_path.exists():
        try:
            with open(main_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            expired, expires_str = config.is_config_expired(cfg)
            accounts.append({
                "id": "main",
                "name": "主账户",
                "is_main": True,
                "enable": cfg.get("enable", True),
                "username": cfg.get("username", ""),
                "expires": cfg.get("expires", ""),
                "expired": expired,
                "expires_str": expires_str
            })
        except Exception as e:
            logger.error(f"Error loading main account config: {e}")

    configs_dir = PROJECT_ROOT / "configs"
    if configs_dir.is_dir():
        for subdir in sorted(configs_dir.iterdir()):
            if subdir.is_dir():
                sub_path = subdir / "config.json"
                if sub_path.exists():
                    try:
                        with open(sub_path, "r", encoding="utf-8") as f:
                            cfg = json.load(f)
                        expired, expires_str = config.is_config_expired(cfg)
                        accounts.append({
                            "id": subdir.name,
                            "name": subdir.name,
                            "is_main": False,
                            "enable": cfg.get("enable", True),
                            "username": cfg.get("username", ""),
                            "expires": cfg.get("expires", ""),
                            "expired": expired,
                            "expires_str": expires_str
                        })
                    except Exception as e:
                        logger.error(f"Error loading sub-account config for {subdir.name}: {e}")
    return accounts

@app.get("/api/accounts/{account_id}")
async def get_account(account_id: str):
    path = get_config_path(account_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Account config not found")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

@app.post("/api/accounts")
async def create_account(req: CreateAccountRequest):
    if req.id == "main":
        raise HTTPException(status_code=400, detail="Cannot create main account")
    path = get_config_path(req.id)
    if path.exists():
        raise HTTPException(status_code=400, detail="Account already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    config_dict = req.config.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, indent=2, ensure_ascii=False)
    cookies_path = path.parent / "cookies.txt"
    with open(cookies_path, "w", encoding="utf-8") as f:
        f.write("")
    return {"status": "success", "message": f"Account {req.id} created"}

@app.put("/api/accounts/{account_id}")
async def update_account(account_id: str, new_config: AccountConfig):
    path = get_config_path(account_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Account config not found")
    config_dict = new_config.model_dump()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, indent=2, ensure_ascii=False)
    return {"status": "success", "message": f"Account {account_id} config updated"}

@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: str):
    if account_id == "main":
        raise HTTPException(status_code=400, detail="Cannot delete main account")
    path = get_config_path(account_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Account not found")
    folder = path.parent
    try:
        shutil.rmtree(folder)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete folder: {e}")
    return {"status": "success", "message": f"Account {account_id} deleted"}

@app.get("/api/accounts/{account_id}/cookies")
async def get_cookies(account_id: str):
    if account_id == "main":
        cookies_path = PROJECT_ROOT / "cookies.txt"
    else:
        path = get_config_path(account_id)
        cookies_path = path.parent / "cookies.txt"
    if not cookies_path.exists():
        return {"cookies": ""}
    with open(cookies_path, "r", encoding="utf-8") as f:
        return {"cookies": f.read()}

@app.put("/api/accounts/{account_id}/cookies")
async def update_cookies(account_id: str, payload: dict = Body(...)):
    cookie_str = payload.get("cookies", "").strip()
    if account_id == "main":
        cookies_path = PROJECT_ROOT / "cookies.txt"
    else:
        path = get_config_path(account_id)
        cookies_path = path.parent / "cookies.txt"
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cookies_path, "w", encoding="utf-8") as f:
        f.write(cookie_str)
    return {"status": "success", "message": "Cookies updated"}

@app.get("/api/accounts/{account_id}/logs")
async def get_logs(account_id: str):
    if account_id == "main":
        log_dir = PROJECT_ROOT / "logs"
    else:
        path = get_config_path(account_id)
        log_dir = path.parent / "logs"
    if not log_dir.is_dir():
        return []
    log_files = []
    for f in log_dir.iterdir():
        if f.is_file() and f.suffix == ".log":
            stat = f.stat()
            log_files.append({
                "filename": f.name,
                "size": stat.st_size,
                "modified": stat.st_mtime
            })
    log_files.sort(key=lambda x: x["modified"], reverse=True)
    return log_files

@app.get("/api/accounts/{account_id}/logs/{filename}")
async def get_log_content(account_id: str, filename: str):
    if not re.match(r"^[a-zA-Z0-9_\-\.]+\.log$", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if account_id == "main":
        log_dir = PROJECT_ROOT / "logs"
    else:
        path = get_config_path(account_id)
        log_dir = path.parent / "logs"
    log_file = log_dir / filename
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
        return {"content": f.read()}

@app.post("/api/accounts/{account_id}/run")
async def run_account_sign_in(account_id: str):
    path = get_config_path(account_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Account not found")
    with open(path, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    config_data["_config_path"] = str(path)
    config_data["_is_main_account"] = (account_id == "main")
    if account_id == "main":
        cookies_path = PROJECT_ROOT / "cookies.txt"
        log_dir = PROJECT_ROOT / "logs"
    else:
        cookies_path = path.parent / "cookies.txt"
        log_dir = path.parent / "logs"
    capture_sink = LogCaptureSink()
    sink_id = logger.add(
        capture_sink.write,
        level="INFO",
        format="{time:HH:mm:ss} | {level: <7} | {message}"
    )
    import argparse
    old_args_global = config.args_global
    config.args_global = argparse.Namespace()
    success = False
    try:
        with sign_in_lock:
            success = run_sign_in(config_data, str(cookies_path), str(log_dir))
    except Exception as e:
        logger.error(f"Manual sign-in failed: {e}")
        capture_sink.logs.append(f"ERROR: {e}")
    finally:
        logger.remove(sink_id)
        config.args_global = old_args_global
    return {
        "success": success,
        "logs": capture_sink.logs
    }

def start_web_server(host: str = "127.0.0.1", port: int = 8000):
    uvicorn.run(app, host=host, port=port)
