# src/config_manager.py (最终导入修正版)

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from .models import AppConfig

# 定义配置文件的路径
CONFIG_DIR = Path(__file__).parent.parent / "config"
CONFIG_FILE_PATH = CONFIG_DIR / "config.json"

def load_config() -> AppConfig:
    """
    加载配置文件。如果目录或文件不存在，则使用默认值自动创建。
    """
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        
        if not CONFIG_FILE_PATH.is_file():
            print("Config file not found. Creating a new one with default values.")
            default_config = AppConfig()
            save_config(default_config)
            return default_config

        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return AppConfig.model_validate(data)
            
    except (json.JSONDecodeError, Exception) as e:
        print(f"Error loading or parsing config file: {e}. Returning a temporary default config.")
        return AppConfig()


def _notify_go_proxy_reload():
    """Tell the Go proxy process to reload config.json (HTTP message, not polling)."""
    if os.environ.get("PROXY_RELOAD_SKIP", "").strip() in ("1", "true", "yes"):
        return
    url = (os.environ.get("PROXY_RELOAD_URL") or "").strip()
    if not url:
        url = "http://127.0.0.1:8000/__internal/reload-config"
    token = (os.environ.get("PROXY_RELOAD_TOKEN") or os.environ.get("EMBY_PROXY_RELOAD_TOKEN") or "").strip()
    try:
        req = urllib.request.Request(url, method="POST")
        if token:
            req.add_header("X-Emby-Virtual-Lib-Reload-Token", token)
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        print(f"[config] notify Go proxy reload HTTP {e.code}: {e.reason}", file=sys.stderr)
    except Exception as e:
        print(f"[config] notify Go proxy reload failed: {e}", file=sys.stderr)


def save_config(config: AppConfig):
    """
    将配置对象安全地保存到文件。
    """
    try:
        CONFIG_DIR.mkdir(exist_ok=True)

        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(config.model_dump_json(by_alias=True, indent=4))
        print(f"Configuration successfully saved to {CONFIG_FILE_PATH}")
        _notify_go_proxy_reload()
    except Exception as e:
        print(f"Error saving config file: {e}")
