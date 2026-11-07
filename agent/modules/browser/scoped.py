"""Conversation-local tabs and facade state over one persistent Chrome profile."""

import asyncio
import contextlib
from uuid import uuid4

from stagehand_middleware.facade import StagehandFacadeTools


# Scope the raw context too: a popup created by another conversation while this
# batch is running must never become visible through context.pages()/page events.
SCOPED_BATCH = """async function scopedBatch(batchStagehand, input) {
  return await (async function executeScoped(run, batchStagehand, input) {
  const allowed = new Set(input.pageIds);
  const raw = batchStagehand.context;
  const socket = new WebSocket(input.browserEndpoint);
  try { await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Browser connection timed out')), 5000);
    socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
    socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Browser connection failed')); }, { once: true });
  }); } catch (error) { socket.close(); throw error; }
  let sequence = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    const item = pending.get(message.id);
    if (!item) return;
    pending.delete(message.id);
    clearTimeout(item.timer);
    if (message.error) item.reject(new Error('Could not read browser targets'));
    else item.resolve(message.result);
  });
  const call = method => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('Browser target lookup timed out')); }, 5000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method }));
  });
  const ownedPages = async () => {
    const targets = (await call('Target.getTargets')).targetInfos;
    for (let pass = 0; pass < targets.length; pass++) {
      let changed = false;
      for (const target of targets) {
        if (!allowed.has(target.targetId) && allowed.has(target.openerId)) {
          allowed.add(target.targetId);
          changed = true;
        }
      }
      if (!changed) break;
    }
    return (await raw.pages()).filter(page => allowed.has(page.pageId));
  };
  const context = new Proxy({}, { get(_target, key) {
    const target = raw;
    if (key === 'pages') return ownedPages;
    if (key === 'newPage') return async (...args) => {
      const page = await target.newPage(...args);
      allowed.add(page.pageId);
      await page.goto('about:blank');
      return page;
    };
    if (key === 'close') return async () => { throw new Error('Use browser.close() to close this conversation'); };
    if (key === 'activePage') return async () => {
      const pages = await ownedPages();
      return pages.find(page => page.pageId === batchStagehand.page.pageId) ?? pages[0] ?? null;
    };
    if (key === 'setActivePage') return async page => {
      if (!(await ownedPages()).some(owned => owned.pageId === page.pageId)) throw new Error('Tab belongs to another conversation');
      return target.setActivePage(page);
    };
    const value = Reflect.get(target, key);
    return typeof value === 'function' ? value.bind(target) : value;
  }});
  const scoped = new Proxy({}, { get(_target, key) {
    if (key === 'context') return context;
    if (key === 'page') return batchStagehand.page;
    if (key === 'evaluateWithShadowRoots' && typeof batchStagehand[key] === 'function') return async (pageId, ...args) => {
      if (!(await ownedPages()).some(page => page.pageId === pageId)) throw new Error('Tab belongs to another conversation');
      return batchStagehand[key](pageId, ...args);
    };
    return undefined;
  }});
  try {
    return { value: await run(scoped, input.values), pageIds: [...allowed] };
  } finally {
    socket.close();
    for (const item of pending.values()) clearTimeout(item.timer);
  }
  })(__CALLBACK__, batchStagehand, input);
}"""


class ScopedFacade(StagehandFacadeTools):
    def __init__(self, driver, artifacts):
        self.driver = driver
        super().__init__(driver.root.stagehand, artifact_root=artifacts,
                         on_close_requested=driver.reopen)

    async def _serialized(self, operation):
        async with self.driver.root.scope_lock:
            self.driver.assert_open()
            await self.driver.reconcile()
            # Supported middleware input; the raw context wrapper additionally
            # filters tabs that appear after the batch starts.
            self._run_input["hiddenPageIds"] = [tab["id"] for tab in await self.driver.root.tabs()
                                                if tab["id"] not in self.driver.page_ids]
            try:
                return await super()._serialized(operation)
            finally:
                if not self.driver.closed and not self.driver.root.closed:
                    await self.driver.reconcile()
                    with contextlib.suppress(Exception):
                        active = await self.driver.root.browser.context.active_page()
                        if active is not None and active.page_id in self.driver.page_ids:
                            self.driver.selected_page_id = active.page_id

    async def _active_page(self):
        return await self.driver.page()

    async def _run_batch(self, source, page):
        return await self._scoped_batch(source, dict(self._run_input), page)

    async def _scoped_batch(self, source, values, page):
        from stagehand_middleware.facade.tools import RUN_BATCH_TIMEOUT_MS

        result = await self.driver.root.stagehand.experimental_batch(
            SCOPED_BATCH.replace("__CALLBACK__", source),
            {"pageIds": list(self.driver.page_ids), "values": values,
             "browserEndpoint": self.driver.root.browser_ws_url},
            timeout=RUN_BATCH_TIMEOUT_MS, page=page,
        )
        for page_id in result["pageIds"]:
            self.driver.claim(page_id)
        return result["value"]


class ScopedDriver:
    """All methods run on the physical driver's loop, including on Windows."""

    def __init__(self, root, artifacts):
        self.root = root
        self.artifacts = artifacts
        self.scope_id = uuid4().hex
        self.page_ids: set[str] = set()
        self.selected_page_id = None
        self.closed = False
        self.facade = ScopedFacade(self, artifacts)

    @classmethod
    async def create(cls, root, artifacts):
        current = cls(root, artifacts)
        async with root.scope_lock:
            root.scopes[current.scope_id] = current
            try:
                await current.new_page()
            except BaseException:
                with contextlib.suppress(Exception):
                    await asyncio.shield(current._close_pages())
                root.scopes.pop(current.scope_id, None)
                current.closed = True
                raise
        return current

    @property
    def port(self):
        return self.root.port

    def assert_open(self):
        if self.closed or self.root.closed:
            raise ConnectionError("Browser session is closed.")

    def claim(self, page_id):
        owner = self.root.page_owners.setdefault(page_id, self.scope_id)
        if owner != self.scope_id:
            raise PermissionError("Browser tab belongs to another conversation.")
        self.page_ids.add(page_id)

    async def new_page(self):
        page = await self.root.browser.context.new_page()
        self.claim(page.page_id)
        self.selected_page_id = page.page_id
        await page.goto("about:blank")
        await page.set_viewport_size(1280, 720)
        return page

    async def reconcile(self):
        self.assert_open()
        targets = await self.root.target_infos()
        live = {target["targetId"] for target in targets}
        for scope in self.root.scopes.values():
            scope.page_ids.intersection_update(live)
        for page_id in set(self.root.page_owners) - live:
            self.root.page_owners.pop(page_id, None)
        # Popups can form a chain and arrive in any order.
        for _ in range(len(targets)):
            changed = False
            for target in targets:
                page_id = target["targetId"]
                opener = self.root.page_owners.get(target.get("openerId"))
                scope = self.root.scopes.get(opener)
                if page_id not in self.root.page_owners and scope is not None:
                    scope.claim(page_id)
                    changed = True
            if not changed:
                break

    async def page(self):
        self.assert_open()
        pages = {page.page_id: page for page in await self.root.browser.context.pages()}
        owned = [page_id for page_id in self.page_ids if page_id in pages]
        if not owned:
            return await self.new_page()
        if self.selected_page_id not in owned:
            self.selected_page_id = sorted(owned)[0]
        return pages[self.selected_page_id]

    async def tabs(self):
        await self.reconcile()
        if not self.page_ids and not self.root.scope_lock.locked():
            async with self.root.scope_lock:
                self.assert_open()
                if not self.page_ids:
                    await self.new_page()
        return [tab for tab in await self.root.tabs() if tab["id"] in self.page_ids]

    async def active_tab_id(self):
        return self.selected_page_id

    async def target_url(self, target_id):
        if target_id not in {tab["id"] for tab in await self.tabs()}:
            raise PermissionError("Browser tab does not belong to this conversation.")
        return await self.root.target_url(target_id)

    async def watch_tabs(self, notify):
        await self.root.watch_tabs(notify)

    async def reset_facade(self, artifacts, active_tab_id=None):
        async with self.root.scope_lock:
            self.assert_open()
            await self.reconcile()
            if active_tab_id is not None:
                owner = self.root.page_owners.get(active_tab_id)
                if owner is not None and owner != self.scope_id:
                    raise PermissionError("Browser tab does not belong to this conversation.")
                self.selected_page_id = active_tab_id if active_tab_id in self.page_ids else None
            await self.page()
            self.artifacts = artifacts
            self.facade = ScopedFacade(self, artifacts)

    async def _close_pages(self):
        pages = await self.root.browser.context.pages()
        for page in pages:
            if page.page_id in self.page_ids:
                await page.close()
        for page_id in self.page_ids:
            self.root.page_owners.pop(page_id, None)
        self.page_ids.clear()

    async def reopen(self):
        # Called inside the facade's shared lock; browser.close() only resets
        # this conversation, never the Chrome process or another session's tabs.
        previous = set(self.page_ids)
        await self.new_page()
        replacement = self.selected_page_id
        self.page_ids = previous
        await self._close_pages()
        self.page_ids = {replacement}
        self.facade = ScopedFacade(self, self.artifacts)

    async def close(self):
        async with self.root.scope_lock:
            if self.closed:
                return
            async with asyncio.timeout(15):
                await self.reconcile()
                await self._close_pages()
            self.closed = True
            self.root.scopes.pop(self.scope_id, None)
