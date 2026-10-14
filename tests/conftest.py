import json
from contextlib import asynccontextmanager

import httpx
import pytest

from autoexam.config import Config
from autoexam.deepseek import DeepSeekProvider
from autoexam.engine import Engine
from autoexam.mock import MockPlatform
from autoexam.mock_server import World
from autoexam.notifications import Notifier
from autoexam.store import Store


@pytest.fixture
def cfg():
    c = Config()
    c.monitor.target_course_id, c.monitor.target_class_id, c.monitor.target_cpi = "1", "2", "3"
    c.exam.dry_run = False
    c.deepseek.max_retries = 0
    c.notification.enabled = False
    return c


@pytest.fixture
def world(cfg):
    return World(cfg.monitor.target())


@pytest.fixture
def harness(tmp_path, cfg):
    @asynccontextmanager
    async def make(world):
        def handler(request):
            payload = json.loads(request.content) if request.content else {}
            path = request.url.raw_path.decode()
            status, body = world.response(request.method, path, payload)
            return httpx.Response(status, json=body)

        pc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ac = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        platform = MockPlatform("http://mock.local", cfg.monitor.target(), client=pc)
        ai = DeepSeekProvider(cfg.deepseek, client=ac, api_key="TEST-KEY-NOT-REAL")
        store = Store(tmp_path / "state.sqlite3")
        notify = Notifier(cfg.notification)
        engine = Engine(cfg, platform, ai, store, notify)
        try:
            yield engine, store, notify, platform, ai
        finally:
            store.close()
            await platform.close()
            await ai.close()

    return make
