import asyncio
from datetime import timedelta

import httpx
import pytest

from autoexam.deepseek import DeepSeekProvider
from autoexam.engine import Engine
from autoexam.mock import MockPlatform, simulated_ai_client
from autoexam.mock_server import MockServer
from autoexam.models import ExamState, NeedsUser, TaskState, utcnow
from autoexam.notifications import Notifier
from autoexam.store import Store


async def test_new_exam_full_chain_and_dedup(harness, world):
    async with harness(world) as (engine, store, notify, *_):
        await engine.tick()
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTED
        assert world.submit_calls == 1 and world.ai_calls == 1
        assert notify.events == ["discovered", "answering", "answered", "submitted", "completion"]


@pytest.mark.parametrize(
    "state", [ExamState.UNPUBLISHED, ExamState.NOT_STARTED, ExamState.EXPIRED, ExamState.UNKNOWN]
)
async def test_non_available_not_entered(harness, world, state):
    world.exams[0].state = state
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert world.enter_calls == world.submit_calls == world.ai_calls == 0
        assert store.get(world.exams[0]) is None


async def test_future_start_even_if_list_says_available(harness, world):
    world.exams[0].starts_at = utcnow() + timedelta(minutes=1)
    async with harness(world) as (engine, *_):
        await engine.tick()
        assert world.enter_calls == 0


async def test_already_submitted(harness, world):
    world.exams[0].state = ExamState.SUBMITTED
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTED
        assert world.submit_calls == world.ai_calls == 0


async def test_empty_list(harness, world):
    world.exams = []
    async with harness(world) as (engine, *_):
        assert await engine.tick() == 0


async def test_session_expired(harness, world):
    world.session_expired = True
    async with harness(world) as (engine, _, _, platform, _):
        with pytest.raises(NeedsUser):
            await platform.health()
        with pytest.raises(NeedsUser):
            await engine.tick()
        assert world.submit_calls == 0


async def test_ai_timeout_never_saves(harness, world):
    async with harness(world) as (engine, store, _, _, ai):

        async def timeout(*_args, **_kw):
            raise TimeoutError

        ai.answer = timeout
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.EXPIRED
        assert world.save_calls == world.submit_calls == 0


async def test_invalid_json_repaired_once(harness, world):
    world.bad_ai = ["{invalid"]
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTED
        assert world.ai_calls == 2


async def test_persistent_invalid_json_stops(harness, world):
    world.bad_ai = ["{invalid", "{invalid"]
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.FAILED
        assert world.ai_calls == 2 and world.submit_calls == 0


async def test_pages_all_extracted(harness, world):
    world.page_size = 1
    async with harness(world) as (_, _, _, platform, _):
        exam = world.exams[0]
        await platform.enter(exam)
        qs = await platform.questions(exam)
        assert [q.question_id for q in qs] == ["1001", "1002"]


async def test_short_network_outage_recovers(harness, world):
    world.network_failures = 1
    async with harness(world) as (engine, store, *_):
        with pytest.raises(httpx.HTTPStatusError):
            await engine.tick()
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTED


async def test_submit_failed_then_no_blind_retry(harness, world):
    world.submit_fails = True
    async with harness(world) as (engine, store, notify, *_):
        await engine.tick()
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMISSION_UNKNOWN
        assert world.submit_calls == 1
        assert "submitted" not in notify.events


async def test_submit_success_response_lost(harness, world):
    world.lost_submit_response = True
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTED
        assert world.submit_calls == 1


@pytest.mark.parametrize("state", [TaskState.SUBMITTING, TaskState.SUBMISSION_UNKNOWN])
async def test_restart_never_resubmits(harness, world, state):
    async with harness(world) as (_, store, *_):
        store.set(world.exams[0], state)
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMISSION_UNKNOWN
        assert world.submit_calls == 0 and world.ai_calls == 0


async def test_restart_after_server_commit(harness, world):
    async with harness(world) as (_, store, *_):
        store.set(world.exams[0], TaskState.SUBMITTING)
    world.exams[0].state = ExamState.SUBMITTED
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTED
        assert world.submit_calls == 0


async def test_restart_during_answering_recover(harness, world):
    world.exams[0].state = ExamState.IN_PROGRESS
    async with harness(world) as (_, store, *_):
        store.set(world.exams[0], TaskState.ANSWERING)
    async with harness(world) as (engine, store, *_):
        await engine.tick(resume_only=True)
        assert store.get(world.exams[0]) == TaskState.SUBMITTED


async def test_multiple_exams(harness, world):
    second = world.exams[0].model_copy(deep=True)
    second.exam_id = "9002"
    world.exams.append(second)
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert all(store.get(e) == TaskState.SUBMITTED for e in world.exams)
        assert world.submit_calls == 2


async def test_dry_run_never_save_or_submit(cfg, harness, world):
    cfg.exam.dry_run = True
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.DRY_RUN
        assert world.ai_calls == 1 and world.save_calls == world.submit_calls == 0


async def test_auto_submit_off(cfg, harness, world):
    cfg.exam.auto_submit = False
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert world.save_calls == 1 and world.submit_calls == 0
        assert store.get(world.exams[0]) == TaskState.NEEDS_USER


async def test_deadline_reserve_prevents_ai(harness, world):
    world.exams[0].deadline = utcnow() + timedelta(seconds=5)
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.EXPIRED
        assert world.ai_calls == world.submit_calls == 0


async def test_unread_image_halts(harness, world):
    world.questions[0].media = ["https://example.invalid/image.png"]
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.NEEDS_USER
        assert world.ai_calls == 0


async def test_saved_answer_mismatch_blocks_submission(harness, world):
    async with harness(world) as (engine, store, _, platform, _):

        async def mismatch(*args):
            return False

        platform.verify_saved = mismatch
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.FAILED
        assert world.submit_calls == 0


async def test_wrong_course_no_actions(harness, world):
    world.exams[0].target = world.target.model_copy(update={"course_id": "999"})
    async with harness(world) as (engine, *_):
        from autoexam.models import CompatibilityError

        with pytest.raises(CompatibilityError):
            await engine.tick()
        assert world.enter_calls == 0


async def test_resume_only_does_not_start_new_exam(harness, world):
    async with harness(world) as (engine, *_):
        await engine.tick(resume_only=True)
        assert world.enter_calls == world.ai_calls == 0


async def test_unknown_server_status_not_http200_confirmation(harness, world):
    world.state_unknown = True
    async with harness(world) as (engine, store, notify, *_):
        # Simulate restart after a submission request; endpoint returns HTTP 200 + UNKNOWN.
        store.set(world.exams[0], TaskState.SUBMITTING)
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMISSION_UNKNOWN
        assert "submitted" not in notify.events


async def test_monitor_start_checks_existing_exam(harness, world):
    async with harness(world) as (engine, store, notify, *_):
        await engine.monitor(lambda: False, once=True)
        assert store.get(world.exams[0]) == TaskState.SUBMITTED
        assert notify.events[0] == "monitor_started" and notify.events[-1] == "monitor_ended"


@pytest.mark.parametrize("lost_response", [False, True])
async def test_real_local_http_server_full_chain(cfg, tmp_path, world, lost_response):
    world.lost_submit_response = lost_response
    with MockServer(world) as server:
        platform = MockPlatform(server.url, cfg.monitor.target())
        ai = DeepSeekProvider(
            cfg.deepseek, client=simulated_ai_client(server.url), api_key="LOCAL-TEST"
        )
        store = Store(tmp_path / "http-state.sqlite3")
        try:
            engine = Engine(cfg, platform, ai, store, Notifier(cfg.notification))
            await engine.monitor(lambda: False, once=True)
            assert store.get(world.exams[0]) == TaskState.SUBMITTED
            assert world.submit_calls == 1
        finally:
            store.close()
            await platform.close()
            await ai.close()


async def test_exit_mid_submit_preserves_reconciliation(harness, world):
    async with harness(world) as (engine, store, _, platform, _):

        async def abrupt(*args):
            world.exams[0].state = ExamState.SUBMITTED
            raise asyncio.CancelledError

        platform.submit = abrupt
        with pytest.raises(asyncio.CancelledError):
            await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTING
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTED
        assert world.submit_calls == 0


async def test_stop_after_ai_does_not_write(harness, world):
    async with harness(world) as (engine, store, _, _, ai):
        original = ai.answer

        async def stopped_after_ai(*args, **kwargs):
            result = await original(*args, **kwargs)
            engine.stop_requested = lambda: True
            return result

        ai.answer = stopped_after_ai
        await engine.tick()
        assert world.save_calls == world.submit_calls == 0
        assert store.get(world.exams[0]) == TaskState.NEEDS_USER


async def test_large_exam_batches(cfg, harness, world):
    from autoexam.mock_server import World

    world.questions = World(count=41).questions
    world.page_size = 20
    async with harness(world) as (engine, store, *_):
        await engine.tick()
        assert store.get(world.exams[0]) == TaskState.SUBMITTED
        assert world.ai_calls == 3  # 20 + 20 + 1, not 41 sequential calls.


async def test_bounded_monitor_outage_stops(cfg, harness, world):
    from autoexam.models import CompatibilityError

    cfg.monitor.max_consecutive_failures = 1
    async with harness(world) as (engine, _, notify, platform, _):

        async def unavailable():
            raise httpx.ConnectTimeout("do not expose credentials")

        platform.list_exams = unavailable
        with pytest.raises(CompatibilityError):
            await engine.monitor(lambda: False)
        assert notify.events[-2:] == ["monitor_failed", "monitor_ended"]
        assert world.submit_calls == 0
