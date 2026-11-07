"""Browser persistence through the application's database session factory."""

import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agent.modules.browser.models import BrowserAccessDenied, BrowserAccountProfile, BrowserArtifact, BrowserBinding, BrowserInvocation, BrowserProfile, BrowserProjectProfile


class BrowserRepository:
    def __init__(self, container):
        self.container = container

    def session(self):
        return self.container._async_session_maker()

    async def binding(self, thread_id: str):
        async with self.session() as session:
            row = await session.scalar(select(BrowserBinding).where(BrowserBinding.thread_id == thread_id))
            return {"thread_id": row.thread_id, "owner_id": row.owner_id, "profile_id": row.profile_id} if row else None

    @staticmethod
    async def _project_default(session, thread_id: str, owner_id: str) -> str | None:
        from agent.modules.workspaces import ThreadWorkspace

        # Delegated threads follow their parent instead of starting on the project's profile.
        if ":sub:" in thread_id:
            return None
        project_id = await session.scalar(select(ThreadWorkspace.project_id).where(ThreadWorkspace.thread_id == thread_id))
        if project_id is not None:
            choice = await session.scalar(select(BrowserProjectProfile).where(
                BrowserProjectProfile.project_id == project_id, BrowserProjectProfile.owner_id == owner_id))
            # Missing legacy project choices retain separate logins, even after setting an account default.
            if choice is None or choice.mode == "separate":
                return None
            if choice.mode == "profile":
                return choice.profile_id
        return await session.scalar(select(BrowserAccountProfile.profile_id).where(BrowserAccountProfile.owner_id == owner_id))

    async def project_default(self, thread_id: str, owner_id: str) -> str | None:
        async with self.session() as session:
            return await self._project_default(session, thread_id, owner_id)

    async def bind(self, thread_id: str, owner_id: str) -> dict:
        async with self.session() as session:
            row = await session.scalar(select(BrowserBinding).where(BrowserBinding.thread_id == thread_id))
            if row is None:
                # Taken once, so a later "This conversation only" choice stays in effect.
                row = BrowserBinding(thread_id=thread_id, owner_id=owner_id,
                                     profile_id=await self._project_default(session, thread_id, owner_id))
                session.add(row)
                await session.commit()
            if row.owner_id != owner_id:
                raise BrowserAccessDenied("This browser belongs to another authenticated account.")
            return {"thread_id": row.thread_id, "owner_id": row.owner_id, "profile_id": row.profile_id}

    async def profile(self, profile_id: str, owner_id: str) -> dict:
        async with self.session() as session:
            row = await session.scalar(select(BrowserProfile).where(
                BrowserProfile.profile_id == profile_id, BrowserProfile.owner_id == owner_id))
            if row is None:
                raise BrowserAccessDenied("Browser profile not found for this account.")
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

    async def rename_profile(self, owner_id: str, profile_id: str, name: str) -> dict:
        await self.profile(profile_id, owner_id)
        async with self.session() as session:
            row = await session.scalar(select(BrowserProfile).where(BrowserProfile.profile_id == profile_id))
            row.name = name
            await session.commit()
            return {"profile_id": row.profile_id, "name": row.name}

    async def project_profile(self, project_id: int, owner_id: str) -> str | None:
        async with self.session() as session:
            return await session.scalar(select(BrowserProjectProfile.profile_id).where(
                BrowserProjectProfile.project_id == project_id, BrowserProjectProfile.owner_id == owner_id))

    async def default_profile(self, owner_id: str) -> str | None:
        async with self.session() as session:
            return await session.scalar(select(BrowserAccountProfile.profile_id).where(BrowserAccountProfile.owner_id == owner_id))

    async def set_default_profile(self, owner_id: str, profile_id: str | None) -> None:
        if profile_id:
            await self.profile(profile_id, owner_id)
        async with self.session() as session:
            insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            await session.execute(insert(BrowserAccountProfile).values(owner_id=owner_id, profile_id=profile_id)
                                  .on_conflict_do_update(index_elements=[BrowserAccountProfile.owner_id],
                                                         set_={"profile_id": profile_id}))
            await session.commit()

    async def project_profile_settings(self, project_id: int, owner_id: str) -> dict:
        from agent.modules.projects import Project

        async with self.session() as session:
            if await session.get(Project, project_id) is None:
                raise LookupError("Project not found.")
            row = await session.scalar(select(BrowserProjectProfile).where(
                BrowserProjectProfile.project_id == project_id, BrowserProjectProfile.owner_id == owner_id))
            default = await session.scalar(select(BrowserAccountProfile.profile_id).where(BrowserAccountProfile.owner_id == owner_id))
            mode, profile_id = (row.mode, row.profile_id) if row else ("separate", None)
            return {"mode": mode, "profile_id": profile_id, "default_profile_id": default,
                    "effective_profile_id": default if mode == "inherit" else profile_id}

    async def profile_projects(self, owner_id: str) -> dict[str, list[dict]]:
        from agent.modules.projects import Project

        async with self.session() as session:
            default = await session.scalar(select(BrowserAccountProfile.profile_id).where(BrowserAccountProfile.owner_id == owner_id))
            rows = (await session.execute(select(BrowserProjectProfile, Project.id, Project.label).join(
                Project, Project.id == BrowserProjectProfile.project_id).where(BrowserProjectProfile.owner_id == owner_id))).all()
            usage: dict[str, list[dict]] = {}
            for choice, project_id, label in rows:
                profile_id = default if choice.mode == "inherit" else choice.profile_id
                if profile_id:
                    usage.setdefault(profile_id, []).append({"id": project_id, "label": label})
            return usage

    async def set_project_profile(self, project_id: int, owner_id: str, profile_id: str | None,
                                  mode: str | None = None) -> None:
        from agent.modules.projects import Project

        mode = mode or ("profile" if profile_id else "separate")
        if mode not in {"inherit", "separate", "profile"} or (mode == "profile") != (profile_id is not None):
            raise ValueError("Select a profile only when the project uses a specific profile.")
        if profile_id:
            await self.profile(profile_id, owner_id)
        async with self.session() as session:
            if await session.get(Project, project_id) is None:
                raise LookupError("Project not found.")
            if mode == "separate":
                await session.execute(delete(BrowserProjectProfile).where(
                    BrowserProjectProfile.project_id == project_id, BrowserProjectProfile.owner_id == owner_id))
            else:
                insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
                await session.execute(insert(BrowserProjectProfile).values(
                    project_id=project_id, owner_id=owner_id, profile_id=profile_id, mode=mode,
                ).on_conflict_do_update(
                    index_elements=[BrowserProjectProfile.project_id, BrowserProjectProfile.owner_id],
                    set_={"profile_id": profile_id, "mode": mode},
                ))
            await session.commit()

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
            defaults = (await session.scalars(select(BrowserAccountProfile).where(
                BrowserAccountProfile.profile_id == profile_id))).all()
            for row in defaults:
                row.profile_id = None
            await session.execute(delete(BrowserProjectProfile).where(BrowserProjectProfile.profile_id == profile_id))
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

    async def pending_handoff(self, thread_id: str, *, include_expired=False):
        async with self.session() as session:
            row = await session.scalar(select(BrowserInvocation).where(
                BrowserInvocation.thread_id == thread_id,
                BrowserInvocation.status.in_(["handoff", "expired"] if include_expired else ["handoff"]))
                .order_by(BrowserInvocation.id.desc()).limit(1))
            if row is None:
                return None
            result = json.loads(row.result)
            return {**result["handoff"], "expired": True} if row.status == "expired" else result

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
                # Keep the request visible until the interrupted workflow consumes its receipt.
                row.status = "expired"
                row.result = json.dumps({"handoff": payload, "receipt": {
                    "handoff_status": "expired", "snapshot_status": "skipped",
                    "content": "Browser handoff expired and the browser was closed automatically. The manual interaction was not confirmed and no fresh snapshot was captured. Explain the timeout and ask whether to restart the browser task before continuing."}})
            if expired:
                await session.commit()
        return expired

    async def finish_handoffs(self, thread_id: str, receipt: dict, *, expired_only: bool = False) -> None:
        statuses = ["expired"] if expired_only else ["handoff", "expired"]
        async with self.session() as session:
            rows = (await session.scalars(select(BrowserInvocation).where(
                BrowserInvocation.thread_id == thread_id, BrowserInvocation.status.in_(statuses)))).all()
            for row in rows:
                row.status = "completed"
                row.result = json.dumps(receipt)
            await session.commit()

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
        return await self.pending_handoff(thread_id, include_expired=True) if binding and binding["owner_id"] == owner_id else None

    async def images(self, owner_id: str, thread_id: str):
        async with self.session() as session:
            rows = (await session.scalars(select(BrowserArtifact).where(
                BrowserArtifact.owner_id == owner_id, BrowserArtifact.thread_id == thread_id)
                .order_by(BrowserArtifact.id.desc()).limit(5))).all()
            return [{"artifact_id": row.artifact_id,
                     "image_url": f"/dashboard-api/browser/artifacts/{row.artifact_id}"} for row in rows]
