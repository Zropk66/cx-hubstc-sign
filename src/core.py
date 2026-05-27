# -*- coding: utf-8 -*-
import datetime
import os
from loguru import logger

from . import config
from .client import (
    _ensure_authenticated_session,
    fetch_mobile_version,
    _exchange_erm_token,
    _get_student_role,
    _fetch_sign_metadata,
    _resolve_location,
    _prepare_photo_object,
    _submit_clock_in,
)
from .notifier import send_notification


def run_sign_in(config_dict: dict, cookies_path: str, log_dir: str) -> bool:
    # 临时更新当前运行的全局配置
    old_config_global = config.config_global.copy() if config.config_global else {}
    config.config_global.update(config_dict)

    os.makedirs(log_dir, exist_ok=True)
    sink_path = os.path.join(log_dir, "log_{time}.log")
    sink_id = logger.add(
        sink_path,
        level="DEBUG",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <7} | {name}:{function}:{line} - {message}",
        retention="7 days"
    )

    try:
        args = config.args_global
        has_args = args is not None and type(args).__name__ not in ('Mock', 'MagicMock', 'NonCallableMagicMock')

        host = config.resolve_value(getattr(args, "host", None) if has_args else None, "host", None, config_dict=config_dict)
        if not host:
            raise ValueError("缺失关键配置: host (请在命令行参数或 config.json 中配置)")

        address = config.resolve_value(getattr(args, "address", None) if has_args else None, "address", None,
                                       config_dict=config_dict)

        lat_val = config.resolve_value(getattr(args, "lat", None) if has_args else None, "lat", None, config_dict=config_dict)
        lat = float(lat_val) if lat_val is not None else 0.0

        lng_val = config.resolve_value(getattr(args, "lng", None) if has_args else None, "lng", None, config_dict=config_dict)
        lng = float(lng_val) if lng_val is not None else 0.0

        photo = config.resolve_value(getattr(args, "photo", None) if has_args else None, "photo", "", config_dict=config_dict)
        device = config.resolve_value(getattr(args, "device", None) if has_args else None, "device", None, config_dict=config_dict)
        if not device:
            raise ValueError("缺失关键配置: device (请在命令行参数或 config.json 中配置)")

        logger.info("=" * 60)
        logger.info(f"{' ' * 26}定位打卡")
        logger.info("=" * 60)

        # 1. 确保用户登录并获取 Session
        session = _ensure_authenticated_session(cookies_path, config_dict)
        if not session:
            return False

        # 2. 获取 mobile 页面版本
        mobile_v = fetch_mobile_version(host)

        # 3. 交换 ermLogin Token
        token = _exchange_erm_token(session, host, mobile_v)
        if not token:
            return False
        session.headers.update({"X-Token": token})
        session.cookies["cx_qmx_token"] = token

        # 4. 获取角色信息
        current_role_id = _get_student_role(session, host, mobile_v)
        if not current_role_id:
            return False
        session.cookies["cx_qmx_role"] = current_role_id

        # 5. 拉取打卡元数据
        batch, cqrq, fetch_status = _fetch_sign_metadata(session, host, mobile_v)
        if fetch_status == "success":
            return True
        elif fetch_status == "fail":
            return False

        # 6. 解析位置要求
        target_lat, target_lng, target_address = _resolve_location(batch, lat, lng, address)

        # 7. 上传/生成签到图片
        try:
            photo_obj = _prepare_photo_object(session, batch, photo)
        except Exception as e:
            logger.error(f"上传照片失败: {e}")
            send_notification("error", f"上传照片失败: {e}")
            return False

        # 8. 构造签到明文并提交打卡
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

        return _submit_clock_in(session, host, mobile_v, punch_params)

    except Exception as e:
        logger.error(f"打卡流程发生未捕获的异常: {e}")
        return False
    finally:
        config.config_global = old_config_global
        logger.remove(sink_id)
