"""启动档案本机扫描服务。"""

from __future__ import annotations

import uvicorn

from app.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
