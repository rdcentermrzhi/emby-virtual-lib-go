"""从仓库根目录运行: python -m admin

等价于 uvicorn，便于本地/IDE 启动（需已安装 admin/requirements.txt）。"""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("ADMIN_HOST", "127.0.0.1")
    port = int(os.environ.get("ADMIN_PORT", "8011"))
    reload = os.environ.get("UVICORN_RELOAD", "").strip().lower() in ("1", "true", "yes")
    uvicorn.run(
        "admin.admin_server:admin_app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
