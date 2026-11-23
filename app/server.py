"""Read Supervisor's protected options before starting the non-root server."""
import os
from pathlib import Path
import sys

import uvicorn

from app.config import from_env


def main() -> None:
    try:
        settings = from_env()
        os.umask(0o077)
        if os.geteuid() == 0:
            if settings.ha_ingress and settings.mode == "deco":
                inventory_dir = Path(settings.database_path).parent
                inventory_dir.mkdir(mode=0o700, exist_ok=True)
                os.chown(inventory_dir, 10001, 10001)
                inventory_dir.chmod(0o700)
            os.setgroups([])
            os.setgid(10001)
            os.setuid(10001)
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))

    from app.main import create_app

    uvicorn.run(create_app(settings), host="0.0.0.0", port=8080, server_header=False, proxy_headers=False)


if __name__ == "__main__":
    main()
