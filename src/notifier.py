# -*- coding: utf-8 -*-
import re
from typing import Union, Any
import requests
from loguru import logger
from . import config


def _get_wechat_credentials(args, has_args) -> tuple[str, str, str | Any, dict[str, str]]:
    app_id = "wx2aae0401b2d24931"
    app_secret = "447916ef9efbef70867646f78ae9dd07"
    template_ids = {
        "full": "DSDf1Qnt2to66t3epzCs5bHjm5czjM3gcWxn1Ujxx_s",
        "simple": "xZbMQQlcZjKTxz3d6xo5Z6byKt3_DwWmy3Ad46yUAn4"
    }

    user_id = getattr(args, "wechat_userid", None) if has_args else None

    cfg = config.config_global
    if not user_id and cfg and "wechat" in cfg and isinstance(cfg["wechat"], dict):
        user_id = cfg["wechat"].get("user_id")

    return app_id, app_secret, user_id or "", template_ids


def send_wechat_notification(content: Union[str, dict] = '') -> bool:
    """
    通过微信测试号模板消息推送状态
    https://mp.weixin.qq.com/debug/cgi-bin/sandboxinfo?action=showinfo&t=sandbox/index
    """
    args = config.args_global
    has_args = args is not None and type(args).__name__ not in ('Mock', 'MagicMock', 'NonCallableMagicMock')
    app_id, app_secret, user_id, template_ids = _get_wechat_credentials(args, has_args)

    if not user_id:
        logger.warning("未配置微信 user_id，跳过微信推送")
        return False

    if isinstance(content, dict):
        payload_data = {
            "content": {
                "value": content.get("content", "暂无详情"),
                "color": "#173177"
            },
            "status": {
                "value": content.get("status", "暂无详情"),
                "color": "#173177"
            }
        }
        template_id = template_ids.get("full")
    else:
        content_str = content or "暂无详情"
        payload_data = {
            "content": {
                "value": content_str,
                "color": "#173177"
            }
        }
        template_id = template_ids.get("simple")

    try:
        from .client import log_http_details
        token_url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={app_id}&secret={app_secret}"
        token_resp = requests.get(token_url, timeout=10)
        log_http_details("GET", token_url, resp=token_resp)
        token_data = token_resp.json()

        access_token = token_data.get("access_token")
        if not access_token:
            logger.error(f"获取微信 access_token 失败: {token_data.get('errmsg', '未知错误')}")
            return False

        send_url = f"https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}"

        payload = {
            "touser": user_id,
            "template_id": template_id,
            "data": payload_data
        }

        resp = requests.post(send_url, json=payload, timeout=10)
        log_http_details("POST", send_url, req_body=payload, resp=resp)
        resp_data = resp.json()

        if resp_data.get("errcode") == 0:
            logger.success("微信测试号模板消息推送成功")
            return True
        else:
            logger.error(f"微信测试号模板消息推送失败: {resp_data.get('errmsg')}")
            return False

    except Exception as e:
        logger.error(f"微信测试号推送过程中发生异常: {e}")
        return False


def _get_bark_credentials(args, has_args) -> tuple[str, str]:
    bark_device_key = getattr(args, "bark_device_key", None) if has_args else None
    bark_device_token = getattr(args, "bark_device_token", None) if has_args else None

    cfg = config.config_global
    if not bark_device_key and cfg and "bark" in cfg and isinstance(cfg["bark"], dict):
        bark_device_key = cfg["bark"].get("device_key")
    if not bark_device_token and cfg and "bark" in cfg and isinstance(cfg["bark"], dict):
        bark_device_token = cfg["bark"].get("device_token")

    return bark_device_key or "", bark_device_token or ""


def send_bark_notification(content: Union[str, dict] = '') -> bool:
    """
    通过 Bark 服务推送消息，支持设备 Token 注册重试机制
    """
    args = config.args_global
    has_args = args is not None and type(args).__name__ not in ('Mock', 'MagicMock', 'NonCallableMagicMock')
    bark_device_key, bark_device_token = _get_bark_credentials(args, has_args)

    if not bark_device_key and not bark_device_token:
        logger.info("未配置推送密钥 (bark_device_key) 和设备 Token (bark_device_token)，跳过推送")
        return False

    if isinstance(content, dict):
        content_val = content.get("content", "")
        status_val = content.get("status", "")
        content = f"信息：{content_val}\n状态：{status_val}"
    else:
        content = f"信息：{content}"

    body = content or "暂无详情"

    def send(key):
        from .client import log_http_details
        url = "https://api.day.app/push"
        payload = {
            "device_key": key,
            "title": "寝室定位打卡",
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
                return True
        except Exception as e:
            logger.warning(f"默认推送密钥 (bark_device_key) 推送失败: {e}")
            if not bark_device_token:
                return False

    if bark_device_token:
        logger.info("尝试使用设备 Token 注册推送...")
        try:
            from .client import log_http_details
            reg_url = f"https://api.day.app/register?devicetoken={bark_device_token}"
            reg_resp = requests.get(reg_url, timeout=10)
            log_http_details("GET", reg_url, resp=reg_resp)
            reg_data = reg_resp.json()
            new_key = reg_data.get("data", {}).get("key") or reg_data.get("key")
            if new_key:
                if send(new_key):
                    logger.success("使用设备 Token 注册并推送成功")
                    config.update_config_value(["bark", "device_key"], new_key)
                    return True
            else:
                raise Exception("未能在注册响应中找到 key")
        except Exception as e:
            logger.error(f"使用设备 Token 注册推送失败: {e}")
    return False


def send_notification(content: Union[str, dict] = ''):
    """
    根据配置分发消息通知。
    """
    args = config.args_global
    has_args = args is not None and type(args).__name__ not in ('Mock', 'MagicMock', 'NonCallableMagicMock')

    enable_notif = config.resolve_value(
        getattr(args, "enable_notification", None) if has_args else None,
        "enable_notification",
        False
    )
    if not enable_notif:
        return

    only_main_notif = config.resolve_value(
        getattr(args, "only_main_notification", None) if has_args else None,
        "only_main_notification",
        False
    )
    if only_main_notif:
        is_main = config.config_global.get("_is_main_account", True)
        if not is_main:
            logger.info("已启用只推送主账号功能，当前为子账号，跳过推送")
            return

    if isinstance(content, dict) and not content.get("status"):
        if "content" in content:
            content = content["content"]
        else:
            content = str(content)

    notif_type = config.resolve_value(
        getattr(args, "notification_type", None) if has_args else None,
        "notification_type",
        None
    )

    if not notif_type:
        raise ValueError("启用了推送通知，但缺失关键配置: notification_type (请在命令行参数或 config.json 中配置)")

    channels = []
    if isinstance(notif_type, list):
        channels = [str(c).strip().lower() for c in notif_type]
    elif isinstance(notif_type, str):
        channels = [c.strip().lower() for c in re.split(r'[,;\s]+', notif_type) if c.strip()]
    else:
        channels = [str(notif_type).strip().lower()]

    for channel in channels:
        if channel == "bark":
            send_bark_notification(content)
        elif channel in ("wechat", "wechat_template"):
            send_wechat_notification(content)
        else:
            logger.warning(f"不支持的推送软件类型: {channel}")
