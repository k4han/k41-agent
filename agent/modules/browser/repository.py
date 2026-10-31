"""Browser persistence through the application's database session factory."""

import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import delete, or_, select

from agent.modules.browser.models import BrowserArtifact, BrowserBinding, BrowserInvocation, BrowserProfile


class BrowserRepository:
    def __init__(self, container):
        self.container = container

    def session(self):
        return self.container._async_session_maker()

    async def binding(self, thread_id: str):
        async with self.session() as session:
            row = await session.scalar(select(BrowserBinding).where(BrowserBinding.thread_id == thread_id))
            return {"thread_id": row.thread_id, "owner_id": row.owner_id, "profile_id": row.profile_id} if row else None

    async def bind(self, thread_id: str, owner_id: str) -> dict:
        async with self.session() as session:
            row = await session.scalar(select(BrowserBinding).where(BrowserBinding.thread_id == thread_id))
            if row is None:
                row = BrowserBinding(thread_id=thread_id, owner_id=owner_id)
                session.add(row)
                await session.commit()
            if row.owner_id != owner_id:
                raise PermissionError("This browser belongs to another authenticated account.")
            return {"thread_id": row.thread_id, "owner_id": row.owner_id, "profile_id": row.profile_id}

    async def profile(self, profile_id: str, owner_id: str) -> dict:
        async with self.session() as session:
            row = await session.scalar(select(BrowserProfile).where(
                BrowserProfile.profile_id == profile_id, BrowserProfile.owner_id == owner_id))
            if row is None:
                raise PermissionError("Browser profile not found for this account.")
            return {"profile_id": row.profile_id, "name": row.name}

    async def profiles(self, owner_id: str) -> list[dict]:
        async with self.session() as session:
            rows = (await session.scalars(select(BrowserProfile).where(
                BrowserProfile.owner_id == owner_id).order_by(BrowserProfile.name))).all()
            return [{"profile_id": row.profile_id, "name": row.name} for row in rows]

    async def create_profile(self, owner_id: str, name: str) -> dict:
        async with self.session() as session:
            row = BrowserProfile(profile_id=uuid4().hex, owner_id=owner_id, name=name)
            session.add(row)
            await session.commit()
            return {"profile_id": row.profile_id, "name": row.name}

    async def select_profile(self, thread_id: str, owner_id: str, profile_id: str | None):
        await self.bind(thread_id, owner_id)
        if profile_id:
            await self.profile(profile_id, owner_id)
        async with self.session() as session:
            row = await session.scalar(select(BrowserBinding).where(BrowserBinding.thread_id == thread_id))
            row.profile_id = profile_id
            await session.commit()

    async def delete_profile(self, owner_id: str, profile_id: str):
        await self.profile(profile_id, owner_id)
        async with self.session() as session:
            bindings = (await session.scalars(select(BrowserBinding).where(
                BrowserBinding.profile_id == profile_id))).all()
            for row in bindings:
                row.profile_id = None
            await session.execute(delete(BrowserProfile).where(BrowserProfile.profile_id == profile_id))
            await session.commit()

    @staticmethod
    def invocation_key(thread_id: str, call_id: str) -> str:
        return hashlib.sha256(f"{thread_id}\0{call_id}".encode()).hexdigest()

    async def invocation(self, thread_id: str, call_id: str):
        async with self.session() as session:
            return await session.scalar(select(BrowserInvocation).where(
                BrowserInvocation.invocation_id == self.invocation_key(thread_id, call_id)))

    async def record(self, thread_id: str, call_id: str, arguments_hash: str, status: str, result=None):
        async with self.session() as session:
            key = self.invocation_key(thread_id, call_id)
            row = await session.scalar(select(BrowserInvocation).where(BrowserInvocation.invocation_id == key))
            if row is None:
                row = BrowserInvocation(invocation_id=key, thread_id=thread_id, arguments_hash=arguments_hash)
                session.add(row)
            row.status = status
            row.result = json.dumps(result) if result is not None else None
            await session.commit()

    async def save_artifact(self, owner_id: str, thread_id: str, artifact_id: str, mime_type: str):
        async with self.session() as session:
            session.add(BrowserArtifact(owner_id=owner_id, thread_id=thread_id,
                                       artifact_id=artifact_id, mime_type=mime_type))
            await session.commit()

    async def artifact(self, owner_id: str, artifact_id: str):
        async with self.session() as session:
            return await session.scalar(select(BrowserArtifact).where(
                BrowserArtifact.artifact_id == artifact_id, BrowserArtifact.owner_id == owner_id))

    async def pending_handoff(self, thread_id: str):
        async with self.session() as session:
            row = await session.scalar(select(BrowserInvocation).where(
                BrowserInvocation.thread_id == thread_id, BrowserInvocation.status == "handoff")
                .order_by(BrowserInvocation.id.desc()).limit(1))
            return json.loads(row.result) if row else None

    async def handoff_threads(self) -> set[str]:
        async with self.session() as session:
            return set(await session.scalars(select(BrowserInvocation.thread_id).where(
                BrowserInvocation.status == "handoff")))

    async def expire_handoffs(self, thread_id: str) -> list[dict]:
        """Keep replay receipts while releasing abandoned and legacy handoffs."""
        expired = []
        async with self.session() as session:
            rows = (await session.scalars(select(BrowserInvocation).where(
                BrowserInvocation.thread_id == thread_id, BrowserInvocation.status == "handoff"))).all()
            for row in rows:
                payload = json.loads(row.result)
                if payload.get("expires_at", 0) > time.time():
                    continue
                expired.append(payload)
                row.status = "completed"
                row.result = json.dumps({"content": "Browser handoff expired and was cancelled. Start a new browser task and inspect a fresh snapshot before continuing."})
            if expired:
                await session.commit()
        return expired

    @staticmethod
    def thread_filter(column, thread_id: str):
        return or_(column == thread_id, column.startswith(f"{thread_id}:sub:", autoescape=True))

    async def thread_ids(self, thread_id: str) -> set[str]:
        queries = [select(model.thread_id).where(self.thread_filter(model.thread_id, thread_id))
                   for model in (BrowserBinding, BrowserInvocation, BrowserArtifact)]
        async with self.session() as session:
            return set(await session.scalars(queries[0].union(*queries[1:])))

    async def thread_artifact_ids(self, thread_id: str) -> list[str]:
        async with self.session() as session:
            return list(await session.scalars(select(BrowserArtifact.artifact_id).where(
                self.thread_filter(BrowserArtifact.thread_id, thread_id))))

    async def delete_thread(self, thread_id: str) -> None:
        async with self.session() as session:
            for model in (BrowserArtifact, BrowserInvocation, BrowserBinding):
                await session.execute(delete(model).where(self.thread_filter(model.thread_id, thread_id)))
            await session.commit()

    async def owned_handoff(self, thread_id: str, owner_id: str):
        binding = await self.binding(thread_id)
        return await self.pending_handoff(thread_id) if binding and binding["owner_id"] == owner_id else None

    async def images(self, owner_id: str, thread_id: str):
        async with self.session() as session:
            rows = (await session.scalars(select(BrowserArtifact).where(
                BrowserArtifact.owner_id == owner_id, BrowserArtifact.thread_id == thread_id)
                .order_by(BrowserArtifact.id.desc()).limit(5))).all()
            return [{"artifact_id": row.artifact_id,
                     "image_url": f"/dashboard-api/browser/artifacts/{row.artifact_id}"} for row in rows]
