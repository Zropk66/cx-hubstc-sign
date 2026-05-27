# -*- coding: utf-8 -*-
"""
签到脚本
"""
import argparse
import base64
import datetime
import hashlib
import io
import json
import os
import platform
import re
import sys
import traceback
from pathlib import Path

import requests
from Crypto.Cipher import DES, AES
from Crypto.Util.Padding import pad
from PIL import Image
from requests import Session

if sys.platform.startswith('win') and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from loguru import logger

logger.remove()

logger.add(
    sys.stdout,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}"
)

logger.add(
    "logs/log_{time}.log",
    level="DEBUG",
    encoding="utf-8",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} - {message}",
    retention="7 days"
)

DUMMY_JPG_BYTES = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c$   \'",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01\x7d\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\x92\xa2\xe1\x16\xf1\x09C\x17S\xc2\xa3\xb2\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xed\xfc\x80\xff\xd9'

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 (schild:c69a8a0f7107e385cfaa3a45194f99ef) (device:iPhone13,2) Language/zh-Hant com.ssreader.ChaoXingStudy/ChaoXingStudy_3_6.7.3_ios_phone_202602111700_314 (@Kalimdor)_14898949611323310882",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive"
}

_args_global: argparse.Namespace
_config_global = {}


def log_http_details(method: str, url: str, headers: dict = None, req_body: any = None, resp: requests.Response = None):
    """
    封装 raw HTTP 请求和响应细节，将其记录到 loguru DEBUG 日志中
    """
    lower_url = url.lower()
    if any(ext in lower_url for ext in [".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".html",
                                        ".htm"]) or "/static/js/" in lower_url:
        return

    if resp is not None:
        content_type = resp.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            return

    logger.debug("=== HTTP Request Details ===")
    logger.debug(f"Method: {method} | URL: {url}")
    if headers:
        logger.debug(f"Request Headers: {headers}")
    if req_body is not None:
        logger.debug(f"Request Body: {req_body}")

    if resp is not None:
        logger.debug("=== HTTP Response Details ===")
        logger.debug(f"Status Code: {resp.status_code}")
        logger.debug(f"Response Headers: {dict(resp.headers)}")
        logger.debug(f"Response Body: {resp.text}")
    logger.debug("=============================")


def resolve_value(arg_value, config_key, default_val, config_dict=None, type_conv=None):
    """
    统一解析参数顺序：命令行参数 > 环境变量 > config.json 配置文件 > 脚本默认值
    """
    if arg_value is not None:
        return type_conv(arg_value) if type_conv else arg_value

    env_key = config_key.upper()
    env_val = os.environ.get(env_key)
    if env_val is not None and env_val != "":
        return type_conv(env_val) if type_conv else env_val

    cfg = config_dict if config_dict is not None else _config_global
    if cfg and config_key in cfg:
        val = cfg[config_key]
        if val is not None and val != "":
            return type_conv(val) if type_conv else val
    return default_val


def load_cookies(cookies_path: str) -> dict:
    """
    加载 Cookie 文件
    """
    cookies = {}
    if not os.path.exists(cookies_path):
        logger.error(f"未找到 Cookie 文件: {cookies_path}")
        return cookies

    with open(cookies_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    for item in content.split(";"):
        if not item.strip() or "=" not in item:
            continue
        k, v = item.strip().split("=", 1)
        cookies[k] = v
    return cookies


def fetch_mobile_version(host: str) -> str:
    """
    动态获取超星 mobile 页面的版本 hash (v 参数)
    """
    default_v = "3d4d49cd4"
    try:
        index_url = f"https://{host}/mobile/"
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        }
        resp = requests.get(index_url, headers=headers, timeout=10)
        log_http_details("GET", index_url, headers=headers, resp=resp)
        if resp.status_code != 200:
            return default_v

        match = re.search(r'src=["\'](/mobile/static/js/index\.[0-9a-f]+\.js)["\']', resp.text)
        if not match:
            match = re.search(r'(/mobile/static/js/index\.[0-9a-f]+\.js)', resp.text)

        if not match:
            return default_v

        js_path = match.group(1)
        js_url = f"https://{host}{js_path}"

        js_resp = requests.get(js_url, headers=headers, timeout=10)
        log_http_details("GET", js_url, headers=headers, resp=js_resp)
        if js_resp.status_code != 200:
            return default_v

        version_match = re.search(r'e=["\']([0-9a-f]{9})["\']\s*,\s*void\s+0\s*!==\s*e', js_resp.text)
        if version_match:
            v_val = version_match.group(1)
            logger.info(f"动态获取到 mobile 页面版本: {v_val}")
            return v_val
    except Exception as e:
        logger.warning(f"动态获取 mobile 版本失败: {e}，将使用默认版本")
    return default_v


def encrypt_aes_base64(plaintext: str) -> str:
    """
    ChaoXing 登录密码 AES 加密
    """
    key_iv = b"u2oh6Vu^HWe4_AES"
    cipher = AES.new(key_iv, AES.MODE_CBC, key_iv)
    padded = pad(plaintext.encode("utf-8"), AES.block_size, style="pkcs7")
    ciphertext = cipher.encrypt(padded)
    return base64.b64encode(ciphertext).decode("utf-8")


def validate_cookies(cookies: dict) -> bool:
    """
    验证 Cookie 是否有效
    """
    logger.debug("开始校验 Cookie...")
    if not cookies:
        logger.warning("Cookie 校验失败: Cookie 字典为空")
        return False
    if not cookies.get("_uid"):
        logger.warning("Cookie 校验失败: 缺失重要键值 _uid")
        return False

    session = requests.Session()
    session.headers.update(MOBILE_HEADERS)
    session.cookies.update(cookies)

    try:
        url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
        resp = session.post(
            url,
            data=data,
            timeout=10,
        )
        log_http_details("POST", url, headers=dict(session.headers), req_body=data, resp=resp)
        if resp.status_code == 200:
            is_passport_redirect = "passport2.chaoxing.com" in resp.text
            has_login_keyword = "login" in resp.text.lower()
            if not is_passport_redirect and not has_login_keyword:
                logger.success("Cookie 校验成功: 用户处于登录状态")
                return True
            else:
                logger.warning(
                    f"Cookie 校验失败: 检测到未登录或跳转。特征: passport2.chaoxing.com={is_passport_redirect}, login_keyword={has_login_keyword}")
                logger.debug(f"跳转/未登录响应内容预览 (前1000字符): {resp.text[:1000]}")
        else:
            logger.warning(f"Cookie 校验异常: 服务器返回状态码 {resp.status_code}")
            logger.debug(f"状态码异常响应内容: {resp.text}")
    except Exception as e:
        logger.warning(f"校验 Cookie 请求异常: {e}")

    return False


def login_chaoxing(username, password) -> Session | None:
    """
    使用手机号/用户名和密码登录超星
    """
    session = requests.Session()
    url = "https://passport2.chaoxing.com/fanyalogin"
    data = {
        "fid": "-1",
        "uname": encrypt_aes_base64(username),
        "password": encrypt_aes_base64(password),
        "refer": "https%3A%2F%2Fi.chaoxing.com",
        "t": True,
        "forbidotherlogin": 0,
        "validate": "",
        "doubleFactorLogin": 0,
        "independentId": 0,
    }

    headers = {
        **MOBILE_HEADERS,
        "Content-Type": "application/x-www-form-urlencoded"
    }

    logger.info("正在发送登录请求...")
    try:
        resp = session.post(url, headers=headers, data=data, timeout=15)
        log_http_details("POST", url, headers=headers, req_body=data, resp=resp)
        resp_data = resp.json()
        if resp_data.get("status"):
            logger.success("登录成功！")
            return session
        else:
            msg = resp_data.get("msg2") or resp_data.get("msg") or "未知错误"
            logger.error(f"登录失败: {msg}")
    except Exception as e:
        logger.error(f"登录请求出错: {e}")
    return None


def save_cookies(cookies_path: str, session: requests.Session):
    """
    保存 Cookie 到指定路径
    """
    cookie_str = ";".join([f"{k}={v}" for k, v in session.cookies.items()])
    try:
        with open(cookies_path, "w", encoding="utf-8") as f:
            f.write(cookie_str)
        logger.success(f"Cookie 已成功保存至: {cookies_path}")
    except Exception as e:
        logger.error(f"保存 Cookie 失败: {e}")


def encrypt_des_hex(plaintext: str, key_str: str = "QRCODENC") -> str:
    """
    DES Hex 加密
    """
    key = key_str.encode("utf-8")
    cipher = DES.new(key, DES.MODE_ECB)
    padded = pad(plaintext.encode("utf-8"), DES.block_size, style="pkcs7")
    ciphertext = cipher.encrypt(padded)
    return ciphertext.hex()


def perform_image_upload(session: requests.Session, image_bytes: bytes, filename: str) -> dict:
    """
    模拟图片上传到 pan-yz.chaoxing.com 的过程
    """
    puid = session.cookies.get("UID", "")
    if not puid:
        puid = session.cookies.get("_uid", "")

    logger.info("图片上传 - 步骤 1: 获取上传 Token...")
    token_url = "https://pan-yz.chaoxing.com/api/token/uservalid"
    token_resp = session.get(token_url, headers=MOBILE_HEADERS, timeout=15)
    log_http_details("GET", token_url, headers=MOBILE_HEADERS, resp=token_resp)
    token_data = token_resp.json()
    if not token_data.get("result"):
        raise Exception("获取上传 Token 失败: " + str(token_data))

    token = token_data.get("_token")
    logger.info(f"成功获取上传 Token: {token[:10]}...")

    md5 = hashlib.md5(image_bytes).hexdigest()
    logger.info(f"图片上传 - 步骤 2: 检查云端文件是否存在, Hash: {md5}...")

    status_url = f"https://pan-yz.chaoxing.com/api/crcStorageStatus?puid={puid}&_token={token}&crc={md5}"
    status_resp = session.get(status_url, headers=MOBILE_HEADERS, timeout=15)
    log_http_details("GET", status_url, headers=MOBILE_HEADERS, resp=status_resp)
    status_data = status_resp.json()

    if status_data.get("result") and status_data.get("exist"):
        file_info = status_data.get("data", {})
        logger.success("云端已存在匹配文件！直接复用...")
        return {
            "objectId": file_info.get("objectId") or file_info.get("objectid"),
            "name": file_info.get("name") or filename,
            "suffix": file_info.get("suffix") or "jpg"
        }

    logger.info("图片上传 - 步骤 3: 上传新文件...")
    upload_url = "https://pan-yz.chaoxing.com/upload?_from=mobilelearn"

    files = {
        "file": (filename, image_bytes, "image/jpeg")
    }
    data = {
        "puid": puid,
        "_token": token,
        "prdid": "447"
    }

    upload_headers = {
        **MOBILE_HEADERS,
        "Host": "pan-yz.chaoxing.com",
        "Origin": "https://mobilelearn.chaoxing.com",
        "Referer": "https://mobilelearn.chaoxing.com/"
    }

    upload_resp = session.post(upload_url, headers=upload_headers, data=data, files=files, timeout=30)
    log_http_details("POST", upload_url, headers=upload_headers, req_body=data, resp=upload_resp)
    upload_data = upload_resp.json()

    if not upload_data.get("result"):
        raise Exception("上传图片失败: " + str(upload_data))

    logger.success("图片上传成功！")
    return upload_data.get("data", {})


def send_bark_notification(status: str, content: str = ''):
    """
    通过 Bark 服务推送消息，支持设备 Token 注册重试机制
    """
    if _args_global:
        bark_device_key = resolve_value(_args_global.bark_device_key, "bark_device_key", "")
        bark_device_token = resolve_value(_args_global.bark_device_token, "bark_device_token", "")
    else:
        config_path = os.path.join(os.getcwd(), "config.json")
        cfg = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                traceback.print_exc()
        bark_device_key = resolve_value(None, "bark_device_key", "", config_dict=cfg)
        bark_device_token = resolve_value(None, "bark_device_token", "", config_dict=cfg)

    if not bark_device_key and not bark_device_token:
        logger.info("未配置推送密钥 (bark_device_key) 和设备 Token (bark_device_token)，跳过推送")
        return

    status_map = {
        "success": "✅",
        "error": "❌",
        "info": "🔔"
    }
    emoji = status_map.get(status.lower() if hasattr(status, "lower") else str(status), "🔔")
    title = "寝室定位打卡"
    body = f"{emoji} {content}" if content else f"{emoji} 暂无详情"

    def send(key):
        url = "https://api.day.app/push"
        payload = {
            "device_key": key,
            "title": title,
            "body": body,
            "group": "超星自动签到"
        }
        resp = requests.post(url, json=payload, timeout=10)
        log_http_details("POST", url, req_body=payload, resp=resp)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text[:100]}")
        return True

    if bark_device_key:
        try:
            if send(bark_device_key):
                logger.success("Bark 消息推送成功")
                return
        except Exception as e:
            logger.warning(f"默认推送密钥 (bark_device_key) 推送失败: {e}")
            if not bark_device_token:
                return

    if bark_device_token:
        logger.info("尝试使用设备 Token 注册推送...")
        try:
            reg_url = f"https://api.day.app/register?devicetoken={bark_device_token}"
            reg_resp = requests.get(reg_url, timeout=10)
            log_http_details("GET", reg_url, resp=reg_resp)
            reg_data = reg_resp.json()
            new_key = reg_data.get("data", {}).get("key") or reg_data.get("key")
            if new_key:
                if send(new_key):
                    logger.success("使用设备 Token 注册并推送成功")
                    return
            else:
                raise Exception("未能在注册响应中找到 key")
        except Exception as e:
            logger.error(f"使用设备 Token 注册推送失败: {e}")


def has_cli_overrides(args: argparse.Namespace) -> bool:
    """
    检查是否传入了非默认的命令行覆盖参数。
    如果传入了如 username, password, address, lat, lng, cookies 等自定义打卡参数，则返回 True
    """
    override_keys = [
        "cookies", "address", "lat", "lng", "photo",
        "device", "username", "password", "bark_device_key", "bark_device_token"
    ]
    for key in override_keys:
        val = getattr(args, key, None)
        if val is not None:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="签到请求脚本。")
    parser.add_argument("--cookies", type=str, default=None, help="Cookie 文件路径")
    parser.add_argument("--host", type=str, default=None, help="签到域名")
    parser.add_argument("--address", type=str, default=None, help="自定义签到地址 (可选)")
    parser.add_argument("--lat", type=float, default=None, help="自定义纬度 (可选)")
    parser.add_argument("--lng", type=float, default=None, help="自定义经度 (可选)")
    parser.add_argument("--photo", type=str, default=None, help="要上传的照片文件路径 (可选)")
    parser.add_argument("--device", type=str, default=None, help="模拟设备名称")
    parser.add_argument("--bark-device-key", type=str, default=None, help="Bark 推送密钥 (可选)")
    parser.add_argument("--bark-device-token", type=str, default=None, help="Bark 设备 Token (可选)")
    parser.add_argument("--username", type=str, default=None, help="用户名/手机号 (可选)")
    parser.add_argument("--password", type=str, default=None, help="密码 (可选)")

    args = parser.parse_args()
    global _args_global, _config_global
    _args_global = args

    logger.debug("=== Running Environment Metadata ===")
    logger.debug(f"OS: {platform.system()} {platform.release()} ({sys.platform})")
    logger.debug(f"Python Version: {platform.python_version()}")
    is_github_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    logger.debug(f"Is GitHub Actions: {is_github_actions}")
    if is_github_actions:
        logger.debug(f"GitHub Repository: {os.environ.get('GITHUB_REPOSITORY')}")
        logger.debug(f"GitHub Workflow: {os.environ.get('GITHUB_WORKFLOW')}")
        logger.debug(f"GitHub Run ID: {os.environ.get('GITHUB_RUN_ID')}")
    logger.debug("=====================================")

    config_data = {}
    config_path = os.path.join(os.getcwd(), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
            _config_global = config_data
            logger.success(f"成功读取配置文件: {config_path}")
        except Exception as e:
            logger.warning(f"读取 config.json 失败: {e}")

    cookies_path = resolve_value(args.cookies, "cookies", "cookies.txt")
    host = resolve_value(args.host, "host", "hbkjzy.qmx.chaoxing.com")
    address = resolve_value(args.address, "address", "")
    lat = resolve_value(args.lat, "lat", 0.0, type_conv=float)
    lng = resolve_value(args.lng, "lng", 0.0, type_conv=float)
    photo = resolve_value(args.photo, "photo", "")
    device = resolve_value(args.device, "device", "iPhone 12")
    username = resolve_value(args.username, "username", "")
    password = resolve_value(args.password, "password", "")

    logger.info("=" * 60)
    logger.info(f"{' ' * 26}定位打卡")
    logger.info("=" * 60)

    cookies = load_cookies(cookies_path)
    if not cookies or not validate_cookies(cookies):
        logger.warning("Cookie 不存在或已过期，需要重新登录。")
        if not username or not password:
            if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
                logger.error(
                    "检测到在 GitHub Actions 环境中运行，但未检测到账号或密码配置！请在 config.json 中配置 username 和 password。")
                send_bark_notification("error", "GitHub Actions 缺少账号密码配置")
                return
            username = input("请输入手机号/用户名: ").strip()
            password = input("请输入密码: ").strip()
        login_session = login_chaoxing(username, password)
        if login_session:
            save_cookies(cookies_path, login_session)
            cookies = load_cookies(cookies_path)
        else:
            logger.error("登录失败，无法继续签到。")
            send_bark_notification("error", "登录失败，请检查账号密码")
            return

    logger.success(f"成功加载有效 Cookie。UID: {cookies.get('UID', '未知')}")

    mobile_v = fetch_mobile_version(host)

    session = requests.Session()
    session.cookies.update(cookies)

    logger.info("正在交换机构 Token (ermLogin)...")
    login_url = f"https://{host}/pedestal/user/ermLogin"
    login_headers = {
        **MOBILE_HEADERS,
        "Referer": f"https://{host}/mobile/?v={mobile_v}"
    }

    try:
        resp = session.get(login_url, headers=login_headers, timeout=15)
        log_http_details("GET", login_url, headers=login_headers, resp=resp)
        resp_data = resp.json()
        if not resp_data.get("success"):
            logger.error(f"Token 交换失败: {resp_data}")
            send_bark_notification("error",
                                   f"Token 交换失败: {resp_data.get('msg', '未知错误')}")
            return

        token = resp_data["data"]["token"]
        logger.success(f"成功获取 Token: {token[:15]}...")
    except Exception as e:
        logger.error(f"Token 交换过程中出错: {e}")
        send_bark_notification("error", f"Token 交换异常: {e}")
        return

    session.headers.update({"X-Token": token})
    session.cookies["cx_qmx_token"] = token

    logger.info("正在获取学生角色信息 (getInfox)...")
    info_url = f"https://{host}/pedestal/user/getInfox?id="
    info_headers = {
        **MOBILE_HEADERS,
        "Referer": f"https://{host}/mobile/?v={mobile_v}"
    }
    try:
        resp = session.get(info_url, headers=info_headers, timeout=15)
        resp_data = resp.json()
        if not resp_data.get("success"):
            logger.error(f"获取角色信息失败: {resp_data}")
            send_bark_notification("error", "获取学生角色信息失败")
            return

        current_role_id = resp_data["data"]["currentRoleId"]
        logger.success(f"成功获取当前角色 ID: {current_role_id}")
        session.cookies["cx_qmx_role"] = current_role_id
    except Exception as e:
        logger.error(f"获取角色信息时出错: {e}")
        send_bark_notification("error", f"获取角色信息异常: {e}")
        return

    logger.info("正在拉取签到批次与规则数据...")
    metadata_url = f"https://{host}/housemaster/sg/roomCheckPunch/getStudentInfo?cqfs=1"
    metadata_headers = {
        **MOBILE_HEADERS,
        "Referer": f"https://{host}/mobile/?v={mobile_v}"
    }
    try:
        resp = session.get(metadata_url, headers=metadata_headers, timeout=15)
        log_http_details("GET", metadata_url, headers=metadata_headers, resp=resp)
        resp_data = resp.json()
        if not resp_data.get("success"):
            if str(resp_data.get("code"))[:3] == "200":
                logger.info(resp_data.get("message"))
                send_bark_notification("info", resp_data.get("message"))
                return
            else:
                send_bark_notification("error", "获取签到元数据失败")
            return

        meta_data = resp_data.get("data", {})
        batch = meta_data.get("batch")
        cqrq = meta_data.get("cqrq", datetime.date.today().strftime("%Y-%m-%d"))

        if not batch:
            logger.info("当前没有处于活动状态的签到批次。")
            msg = "当前没有活动状态的签到批次。"
            if meta_data.get("result") and meta_data.get("result", {}).get("jg"):
                logger.info(
                    f"提示: 今日已签到状态为: {meta_data['result']['jg']} (时间: {meta_data['result'].get('sj')})")
                msg += f"\n今日已签到状态: {meta_data['result']['jg']} (时间: {meta_data['result'].get('sj')})"
            send_bark_notification("info", msg)
            return

        logger.success("发现进行中的签到批次:")
        logger.info(f"    批次 ID (pcId): {batch.get('id')}")
        logger.info(f"    楼栋 ID (ldId): {batch.get('ldId')}")
        logger.info(f"    床位 ID (cwId): {batch.get('cwId')}")
        logger.info(f"    学生 ID (xsId): {batch.get('xsId')}")
        logger.info(f"    签到日期 (rq): {cqrq}")
        logger.info(f"    签到规则状态: {batch.get('status')}")
    except Exception as e:
        logger.error(f"获取签到元数据时出错: {e}")
        send_bark_notification("error", f"获取签到元数据异常: {e}")
        return

    target_lat = lat
    target_lng = lng
    target_address = address

    logger.debug(f"输入参数 - lat: {lat}, lng: {lng}, address: '{address}'")
    if not target_lat or not target_lng or not target_address:
        raw_qdwz = batch.get("qdwz", "[]")
        logger.debug(f"从批次获取到的原始 qdwz 位置配置: {raw_qdwz}")
        logger.info("正在从批次规则中解析位置要求...")
        try:
            allowed_locations = json.loads(raw_qdwz)
            logger.debug(f"解析后的位置规则列表共包含 {len(allowed_locations)} 个候选位置")
            if allowed_locations:
                for idx, loc in enumerate(allowed_locations):
                    logger.debug(
                        f"候选位置 {idx}: name='{loc.get('name')}', lat={loc.get('lat')}, lng={loc.get('lng')}")
                loc = allowed_locations[0]
                logger.success(f"找到允许的签到位置规则: {loc.get('name')}")
                if not target_lat:
                    target_lat = loc.get("lat")
                    logger.debug(f"采用规则纬度 lat: {target_lat}")
                if not target_lng:
                    target_lng = loc.get("lng")
                    logger.debug(f"采用规则经度 lng: {target_lng}")
                if not target_address:
                    target_address = f"湖北省武汉市洪山区软件园东路，湖北科技职业学院(关山校区)内，{loc.get('name')}"
                    logger.debug(f"采用规则地址 address: '{target_address}'")
            else:
                logger.info("批次中未配置任何位置规则，使用默认位置。")
        except Exception as e:
            logger.warning(f"解析位置规则失败: {e}")

    if not target_lat or not target_lng or not target_address:
        target_lat = 30.477347181407705
        target_lng = 114.41138545505815
        target_address = "湖北省武汉市洪山区软件园东路，湖北科技职业学院(关山校区)"
        logger.debug("因未解析到规则位置或参数不全，采用预设默认学校坐标")

    logger.success(f"最终采用位置: {target_address} ({target_lat}, {target_lng})")

    photo_obj = None
    if batch.get("status") == "1":
        logger.warning("规则提示: 此批次要求进行 照片 签到。")
        image_bytes = None

        if photo and os.path.exists(photo):
            photo_path = Path(photo)
            photo_name = photo_path.name
            with open(photo_path, "rb") as f:
                image_bytes = f.read()
            logger.info(f"正在从该路径加载照片: {photo_path}")
        else:
            logger.info("未提供自定义照片，自动生成一张符合手机拍照比例(3:4，如 600x800)的纯黑 JPEG 图片...")
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H%M%S%f")[:-3]
            photo_name = f"{now_str}.jpg"
            try:
                img = Image.new('RGB', (600, 800), color='black')
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='JPEG')
                image_bytes = img_byte_arr.getvalue()
                logger.success("成功利用 Pillow 生成 600x800 纯黑图片")
            except Exception as e:
                logger.warning(f"使用 Pillow 生成图片失败: {e}，回退到 1x1 像素的占位图...")
                image_bytes = DUMMY_JPG_BYTES

        try:
            uploaded_info = perform_image_upload(session, image_bytes, photo_name)
            object_id = uploaded_info.get("objectId") or uploaded_info.get("objectid")
            if not object_id:
                raise Exception("返回的数据不包含 objectId: " + str(uploaded_info))

            photo_obj = {
                "type": "jpg",
                "objectid": object_id,
                "name": uploaded_info.get("name") or photo_name,
                "url": f"https://p.ananas.chaoxing.com/star3/origin/{object_id}.jpg"
            }
            logger.success(f"照片对象构建成功: objectid={object_id}")
        except Exception as e:
            logger.error(f"上传照片失败: {e}")
            send_bark_notification("error", f"上传照片失败: {e}")
            return

    now = datetime.datetime.now()
    punch_time = now.strftime("%Y-%m-%d %H:%M:%S")

    punch_params = {
        "jg": "1",
        "sj": punch_time,
        "rq": cqrq,
        "pcId": batch.get("id"),
        "ldId": batch.get("ldId"),
        "cwId": batch.get("cwId"),
        "xsId": batch.get("xsId"),
        "cqfs": "1",
        "tp": photo_obj,
        "dkwz": target_address
    }
    if device:
        punch_params["lysbmc"] = device

    logger.debug(f"签到明文载荷: {json.dumps(punch_params, ensure_ascii=False, indent=2)}")

    plaintext_str = json.dumps(punch_params, ensure_ascii=False, separators=(',', ':'))
    encrypted_hex = encrypt_des_hex(plaintext_str, "QRCODENC")
    logger.debug(f"加密密文十六进制 (长度={len(encrypted_hex)}): {encrypted_hex}")

    logger.info("正在提交打卡请求...")
    clockin_url = f"https://{host}/housemaster/sg/roomCheckPunch/clockIn"

    clockin_headers = {
        **MOBILE_HEADERS,
        "Content-Type": "application/json; charset=utf-8",
        "Origin": f"https://{host}",
        "Referer": f"https://{host}/mobile/?v={mobile_v}"
    }

    clockin_data = {
        "jsonStr": encrypted_hex
    }

    try:
        resp = session.post(clockin_url, headers=clockin_headers, json=clockin_data, timeout=15)
        log_http_details("POST", clockin_url, headers=clockin_headers, req_body=clockin_data, resp=resp)

        if resp.status_code == 200:
            try:
                resp_json = resp.json()
                if resp_json.get("success") or resp_json.get("code") == 20000:
                    msg = f"签到已提交！\n反馈: {resp_json.get('msg') or resp_json.get('message') or '成功'}"
                    logger.success(msg)
                    send_bark_notification("success", msg)
                else:
                    msg = f"签到提交失败: {resp_json.get('msg') or resp_json.get('message') or resp.text}"
                    logger.error(msg)
                    send_bark_notification("error", msg)
            except Exception:
                logger.exception("解析打卡返回结果异常")
                send_bark_notification("info", f"HTTP {resp.status_code}\n打卡请求已发出 (无法解析返回结果)")
        else:
            logger.error(f"HTTP 响应状态码异常: {resp.status_code}，响应内容: {resp.text}")
            send_bark_notification("error", f"HTTP {resp.status_code}\n{resp.text[:100]}")
    except Exception as e:
        logger.error(f"提交打卡请求时发生错误: {e}")
        send_bark_notification("error", f"打卡提交异常: {e}")
        return


if __name__ == "__main__":
    start_time = datetime.datetime.now()
    try:
        main()
    finally:
        logger.info("=" * 60)
        logger.info(f"{' ' * 22}打卡流程执行完毕")
        elapsed_time = datetime.datetime.now() - start_time
        logger.info(f"本次运行共耗时: {elapsed_time}")
        logger.info("=" * 60)
