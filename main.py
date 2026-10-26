"""Entry point: `uv run main.py` serves the dashboard on localhost."""

import os

import uvicorn

from app.bootstrap import create_app

app = create_app()


def main() -> None:
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8765"))
    print(f"OpsDeck ▸ http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
