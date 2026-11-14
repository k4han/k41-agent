from agent.bootstrap.app import run


def __getattr__(name: str):
    if name == "app":
        from agent.bootstrap.app import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["app", "run"]


if __name__ == "__main__":
    run()
