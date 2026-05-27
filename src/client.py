# -*- coding: utf-8 -*-
import datetime
import hashlib
import io
import json
import os
import re
from pathlib import Path

import requests
from PIL import Image
from loguru import logger

from . import config
from .crypto import encrypt_aes_base64, encrypt_des_hex
from .notifier import send_notification

DUMMY_JPG_BYTES = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c$   \'",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01\x7d\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\x92\xa2\xe1\x16\xf1\x09C\x17S\xc2\xa3\xb2\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xed\xfc\x80\xff\xd9'

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 (schild:c69a8a0f7107e385cfaa3a45194f99ef) (device:iPhone13,2) Language/zh-Hant com.ssreader.ChaoXingStudy/ChaoXingStudy_3_6.7.3_ios_phone_202602111700_314 (@Kalimdor)_14898949611323310882",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive"
}


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


def login_chaoxing(username, password) -> requests.Session | None:
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


def _ensure_authenticated_session(cookies_path: str, config_dict: dict) -> requests.Session | None:
    """
    确保用户会话处于已登录状态。如果 Cookie 失效，则进行登录并更新保存 Cookie。
    """
    args = config.args_global
    has_args = args is not None and type(args).__name__ not in ('Mock', 'MagicMock', 'NonCallableMagicMock')
    username = config.resolve_value(getattr(args, "username", None) if has_args else None, "username", "", config_dict=config_dict)
    password = config.resolve_value(getattr(args, "password", None) if has_args else None, "password", "", config_dict=config_dict)

    cookies = load_cookies(cookies_path)
    if not cookies or not validate_cookies(cookies):
        logger.warning("Cookie 不存在或已过期，需要重新登录。")
        if not username or not password:
            if os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"):
                logger.error(
                    "检测到在 GitHub Actions 环境中运行，但未检测到账号或密码配置！请在 config.json 中配置 username 和 password。")
                send_notification("error", "GitHub Actions 缺少账号密码配置")
                return None
            username = input("请输入手机号/用户名: ").strip()
            password = input("请输入密码: ").strip()
        login_session = login_chaoxing(username, password)
        if login_session:
            save_cookies(cookies_path, login_session)
            cookies = load_cookies(cookies_path)
        else:
            logger.error("登录失败，无法继续签到。")
            send_notification("error", "登录失败，请检查账号密码")
            return None

    logger.success(f"成功加载有效 Cookie。UID: {cookies.get('UID', '未知')}")
    session = requests.Session()
    session.cookies.update(cookies)
    return session


def _exchange_erm_token(session: requests.Session, host: str, mobile_v: str) -> str | None:
    """
    交换机构 Token (ermLogin)
    """
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
            send_notification("error", f"Token 交换失败: {resp_data.get('msg', '未知错误')}")
            return None

        token = resp_data["data"]["token"]
        logger.success(f"成功获取 Token: {token[:15]}...")
        return token
    except Exception as e:
        logger.error(f"Token 交换过程中出错: {e}")
        send_notification("error", f"Token 交换异常: {e}")
        return None


def _get_student_role(session: requests.Session, host: str, mobile_v: str) -> str | None:
    """
    获取学生角色信息 (getInfox)
    """
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
            send_notification("error", "获取学生角色信息失败")
            return None

        current_role_id = resp_data["data"]["currentRoleId"]
        logger.success(f"成功获取当前角色 ID: {current_role_id}")
        return current_role_id
    except Exception as e:
        logger.error(f"获取角色信息时出错: {e}")
        send_notification("error", f"获取角色信息异常: {e}")
        return None


def _fetch_sign_metadata(session: requests.Session, host: str, mobile_v: str) -> tuple[
    dict | None, str | None, str | None]:
    """
    拉取签到批次与规则数据。
    """
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
                send_notification("info", resp_data.get("message"))
                return None, None, "success"
            else:
                send_notification("error", "获取签到元数据失败")
                return None, None, "fail"

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
            send_notification("info", msg)
            return None, cqrq, "success"

        logger.success("发现进行中的签到批次:")
        logger.info(f"    批次 ID (pcId): {batch.get('id')}")
        logger.info(f"    楼栋 ID (ldId): {batch.get('ldId')}")
        logger.info(f"    床位 ID (cwId): {batch.get('cwId')}")
        logger.info(f"    学生 ID (xsId): {batch.get('xsId')}")
        logger.info(f"    签到日期 (rq): {cqrq}")
        logger.info(f"    签到规则状态: {batch.get('status')}")
        return batch, cqrq, None
    except Exception as e:
        logger.error(f"获取签到元数据时出错: {e}")
        send_notification("error", f"获取签到元数据异常: {e}")
        return None, None, "fail"


def _resolve_location(batch: dict, lat: float, lng: float, address: str) -> tuple[float, float, str]:
    """
    解析最终采用的定位经纬度与地址信息
    """
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
        raise ValueError(
            "无法获取有效的签到位置信息 (未在命令行参数、配置文件 config.json 或签到规则中找到 lat/lng/address)")

    logger.success(f"最终采用位置: {target_address} ({target_lat}, {target_lng})")
    return target_lat, target_lng, target_address


def _prepare_photo_object(session: requests.Session, batch: dict, photo: str) -> dict | None:
    """
    若批次规则要求照片签到，则加载/生成并上传照片，返回构建后的照片对象字典；否则返回 None。
    """
    if batch.get("status") != "1":
        return None

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
    return photo_obj


def _submit_clock_in(session: requests.Session, host: str, mobile_v: str, punch_params: dict) -> bool:
    """
    加密签到参数，提交打卡请求，并处理返回结果及通知
    """
    import json
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
                    send_notification("success", msg)
                    return True
                else:
                    msg = f"签到提交失败: {resp_json.get('msg') or resp_json.get('message') or resp.text}"
                    logger.error(msg)
                    send_notification("error", msg)
                    return False
            except Exception:
                logger.exception("解析打卡返回结果异常")
                send_notification("info", f"HTTP {resp.status_code}\n打卡请求已发出 (无法解析返回结果)")
                return False
        else:
            logger.error(f"HTTP 响应状态码异常: {resp.status_code}，响应内容: {resp.text}")
            send_notification("error", f"HTTP {resp.status_code}\n{resp.text[:100]}")
            return False
    except Exception as e:
        logger.error(f"提交打卡请求时发生错误: {e}")
        send_notification("error", f"打卡提交异常: {e}")
        return False
