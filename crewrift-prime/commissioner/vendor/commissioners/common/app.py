from __future__ import annotations

from os import getenv

from fastapi import FastAPI

from commissioners.common.commissioners import get_commissioner
from commissioners.common.server import create_app


def commissioner_app(key: str | None = None) -> FastAPI:
    return create_app(get_commissioner(key or getenv("COMMISSIONER_KEY", "config_driven")))


def run(app: FastAPI) -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)


app = commissioner_app()


def main() -> None:
    run(app)


if __name__ == "__main__":
    main()
