# -*- coding: utf-8 -*-
import argparse
import datetime
import os
import platform
import sys
import time
from pathlib import Path
from loguru import logger

from . import config
from .config import has_cli_overrides, is_config_expired, _load_config_file
from .core import run_sign_in
from .notifier import send_notification

# 配置 stdout 编码以支持 Windows 环境下的 UTF-8
if sys.platform.startswith('win') and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def configure_logging():
    """
    配置默认的日志记录器格式与输出级别
    """
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}"
    )


def main():
    configure_logging()

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
    parser.add_argument("--notification-type", type=str, default=None, help="推送软件类型 (可选, 默认为 bark)")
    parser.add_argument("--enable-notification", dest="enable_notification", action="store_true", default=None,
                        help="启用推送通知")
    parser.add_argument("--no-notification", dest="enable_notification", action="store_false", default=None,
                        help="禁用推送通知")
    parser.add_argument("--only-main-notification", dest="only_main_notification", action="store_true", default=None,
                        help="仅允许主账号发送推送通知（无视所有子账号的推送）")
    parser.add_argument("--wechat-userid", type=str, default=None, help="接收消息的微信用户 OpenID (可选)")

    args = parser.parse_args()
    config.args_global = args

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

    # 如果有命令行参数覆盖，则运行单账户模式
    if has_cli_overrides(args):
        logger.info("检测到命令行覆盖参数，使用单账户模式运行")
        config_data = {"_is_main_account": True}
        config_path = os.path.join(os.getcwd(), "config.json")
        if os.path.exists(config_path):
            try:
                config_data = _load_config_file(config_path)
                config_data["_config_path"] = config_path
                config_data["_is_main_account"] = True
                config.config_global = config_data
                logger.success(f"成功读取配置文件: {config_path}")
            except Exception as e:
                logger.warning(f"读取 config.json 失败: {e}")

        if not config_data.get("enable", True):
            logger.info("当前账户已配置为禁用 (enable: false)，跳过该账户")
            return

        # 检查过期时间
        expired, expires_str = is_config_expired(config_data)
        if expired:
            logger.warning(f"当前主账户已于 {expires_str} 过期，跳过打卡流程。")
            config.config_global = config_data
            try:
                send_notification("error", f"账户 主账户 已于 {expires_str} 过期，已停止打卡")
            except Exception as ne:
                logger.error(f"发送过期通知失败: {ne}")
            return

        cookies_path = config.resolve_value(args.cookies, "cookies", "cookies.txt", config_dict=config_data)
        run_sign_in(config_data, cookies_path, "logs")
        return

    accounts_to_run = []

    # 1. 检查根目录 config.json (主账户)
    config_path = os.path.join(os.getcwd(), "config.json")
    if os.path.exists(config_path):
        try:
            root_config = _load_config_file(config_path)
            root_config["_config_path"] = config_path
            root_config["_is_main_account"] = True
            cookies_path = config.resolve_value(None, "cookies", "cookies.txt", config_dict=root_config)
            accounts_to_run.append((root_config, cookies_path, "logs", "主账户"))
        except Exception as e:
            logger.warning(f"读取根目录 config.json 失败: {e}")

    # 2. 检查 configs/ 目录下的子账户配置
    configs_dir = Path(os.getcwd()) / "configs"
    if configs_dir.is_dir():
        subdirs = sorted([d for d in configs_dir.iterdir() if d.is_dir()], key=lambda x: x.name)
        for subdir in subdirs:
            sub_config_path = subdir / "config.json"
            if sub_config_path.exists():
                try:
                    sub_config = _load_config_file(sub_config_path)
                    sub_config["_config_path"] = str(sub_config_path)
                    sub_config["_is_main_account"] = False
                    sub_cookies_path = config.resolve_value(None, "cookies", str(subdir / "cookies.txt"),
                                                             config_dict=sub_config)
                    sub_log_dir = str(subdir / "logs")
                    accounts_to_run.append((sub_config, sub_cookies_path, sub_log_dir, f"子账户({subdir.name})"))
                except Exception as e:
                    logger.warning(f"读取子账户配置失败 ({subdir.name}): {e}")

    if not accounts_to_run:
        logger.error("未找到任何有效配置（根目录 config.json 或 configs/ 下 of 子账户配置）")
        return

    logger.info(f"共发现 {len(accounts_to_run)} 个账户配置，开始顺序执行签到")

    for i, (config_data, cookies_path, log_dir, account_name) in enumerate(accounts_to_run):
        if not config_data.get("enable", True):
            logger.info(f"[{i + 1}/{len(accounts_to_run)}] 账户 {account_name} 已配置为禁用 (enable: false)，跳过该账户")
            continue

        # 检查过期时间
        expired, expires_str = is_config_expired(config_data)
        if expired:
            logger.warning(f"[{i + 1}/{len(accounts_to_run)}] 账户 {account_name} 已于 {expires_str} 过期，跳过该账户")
            config.config_global = config_data
            try:
                send_notification("error", f"账户 {account_name} 已于 {expires_str} 过期，已停止打卡")
            except Exception as ne:
                logger.error(f"发送过期通知失败: {ne}")
            continue

        logger.info(f"[{i + 1}/{len(accounts_to_run)}] 开始处理账户: {account_name}")
        config.config_global = config_data
        try:
            run_sign_in(config_data, cookies_path, log_dir)
        except Exception as e:
            logger.error(f"账户 {account_name} 运行中发生未捕获异常: {e}")
            send_notification("error", f"账户 {account_name} 运行异常: {e}")

        if i < len(accounts_to_run) - 1:
            logger.info("等待 1 秒后执行下一个账户...")
            time.sleep(1)


def run():
    start_time = datetime.datetime.now()
    try:
        main()
    finally:
        logger.info("=" * 60)
        logger.info(f"打卡流程执行完毕")
        elapsed_time = datetime.datetime.now() - start_time
        logger.info(f"本次运行共耗时: {elapsed_time}")
        logger.info("=" * 60)
