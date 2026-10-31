import asyncio
import tempfile
import time
from pathlib import Path

from agent.bootstrap.container import create_test_container
from agent.shared.infrastructure.db import Base, load_orm_models
from agent.shared.infrastructure.db.engine import create_tables


def bench(label, fn):
    started = time.perf_counter()
    fn()
    print(label, round(time.perf_counter() - started, 3), "s")


root = Path(tempfile.gettempdir()) / "k41_db_bench"
root.mkdir(parents=True, exist_ok=True)

load_orm_models()
db_path = root / "schema-only.db"
if db_path.exists():
    db_path.unlink()
bench("create_all 30 tables", lambda: create_tables(f"sqlite:///{db_path.as_posix()}", metadata=Base.metadata))

container = create_test_container(tmp_path=root / "full")
container.activate()
bench("initialize_persistence", lambda: asyncio.run(container.initialize_persistence()))
asyncio.run(container.close_persistence())
