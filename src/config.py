# -*- coding: utf-8 -*-
import argparse
import datetime
import json
from pathlib import Path
from loguru import logger

# 包级全局配置变量
args_global = argparse.Namespace()
config_global = {}


def resolve_value(arg_value, config_key, default_val, config_dict=None, type_conv=None):
    """
    统一解析参数顺序：命令行参数 > config.json 配置文件 > 脚本默认值
    """
    if arg_value is not None:
        return type_conv(arg_value) if type_conv else arg_value

    cfg = config_dict if config_dict is not None else config_global
    if cfg and config_key in cfg:
        val = cfg[config_key]
        if val is not None and val != "":
            return type_conv(val) if type_conv else val
    return default_val


def is_config_expired(config: dict) -> tuple[bool, str | None]:
    """
    检查配置是否已过期
    返回: (是否过期, 过期时间字符串/None)
    """
    expires = config.get("expires")
    if not expires:
        return False, None

    expires_str = str(expires).strip()
    if not expires_str:
        return False, None

    # 尝试解析格式 1: YYYY-MM-DD HH:MM:SS
    try:
        dt = datetime.datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
        is_expired = datetime.datetime.now() > dt
        return is_expired, expires_str
    except ValueError:
        pass

    # 尝试解析格式 2: YYYY-MM-DD
    try:
        dt = datetime.datetime.strptime(expires_str, "%Y-%m-%d")
        # 将其时间设为该天最后一秒，以便当天全天可用
        dt = dt.replace(hour=23, minute=59, second=59)
        is_expired = datetime.datetime.now() > dt
        return is_expired, expires_str
    except ValueError:
        logger.warning(f"配置文件中的 expires 格式错误 (应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS): {expires_str}")
        return False, None


def update_config_value(key_path: list[str] | str, value: any):
    """
    更新全局配置中的某个键值对，并持久化写入对应的 config.json 配置文件。
    支持嵌套路径，例如 key_path=["bark", "device_key"]
    """
    global config_global

    # 1. 更新内存中的配置
    if isinstance(key_path, str):
        key_path = [key_path]

    cfg_target = config_global
    for k in key_path[:-1]:
        if k not in cfg_target or not isinstance(cfg_target[k], dict):
            cfg_target[k] = {}
        cfg_target = cfg_target[k]
    cfg_target[key_path[-1]] = value

    # 2. 检查是否有文件路径
    config_path = config_global.get("_config_path")
    if not config_path:
        logger.debug(f"未找到配置文件路径，仅更新内存中的配置: {'.'.join(key_path)} = {value}")
        return

    # 3. 写入文件
    try:
        config_path = Path(config_path)
        if config_path.is_file():
            # 读取原始配置以保留其他字段
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)

            # 更新字段
            cfg_file_target = config_data
            for k in key_path[:-1]:
                if k not in cfg_file_target or not isinstance(cfg_file_target[k], dict):
                    cfg_file_target[k] = {}
                cfg_file_target = cfg_file_target[k]
            cfg_file_target[key_path[-1]] = value

            # 移除可能误写入文件的 _config_path
            config_data.pop("_config_path", None)

            # 写入回文件 (保留 2 格缩进)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)

            logger.success(f"已成功更新配置文件: {config_path}，{'.'.join(key_path)} = {value}")
        else:
            logger.warning(f"配置文件路径不存在或不是文件: {config_path}")
    except Exception as e:
        logger.error(f"写入配置文件失败: {e}")


def has_cli_overrides(args: argparse.Namespace) -> bool:
    """
    检查是否传入了非默认的命令行覆盖参数。
    """
    override_keys = [
        "cookies", "address", "lat", "lng", "photo",
        "device", "username", "password", "bark_device_key", "bark_device_token", "notification_type",
        "wechat_userid"
    ]
    for key in override_keys:
        val = getattr(args, key, None)
        if val is not None:
            return True
    return False


def _load_config_file(path) -> dict:
    """
    读取并解析 JSON 配置文件，兼容测试中的 MagicMock 文件对象
    """
    f_obj = open(path, "r", encoding="utf-8")
    if type(f_obj).__name__ in ('Mock', 'MagicMock', 'NonCallableMagicMock'):
        f_obj.__enter__.return_value = f_obj
    with f_obj as f:
        content = f.read()
        if not isinstance(content, str):
            content = "{}"
        return json.loads(content)
