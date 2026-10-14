import asyncio
import json
from time import monotonic

import httpx
import pytest

from autoexam.config import DeepSeek
from autoexam.deepseek import DeepSeekProvider, validate_answers
from autoexam.models import DeadlineError, ProviderError


def row(qid="1001", answer=None, uncertain=False):
    return {
        "question_id": qid,
        "answer": ["A"] if answer is None else answer,
        "uncertain": uncertain,
    }


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [row(answer=["A", "B"])],
        [row(answer=["Z"])],
        [row(answer=[])],
        [row(), row()],
        [row(answer=["A", "A"])],
        [dict(row(), uncertain="false")],
    ],
)
def test_invalid_answers_not_accepted(world, rows):
    valid, missing = validate_answers({"answers": rows}, [world.questions[0]])
    assert not valid and len(missing) == 1


def test_multiple_choice_empty_incomplete(world):
    valid, missing = validate_answers({"answers": [row("1002", [])]}, [world.questions[1]])
    assert not valid and missing


def test_multiple_choice_semantic_correctness_not_claimed(world):
    # A non-empty subset may still be wrong/incomplete. Structural validation cannot know.
    valid, missing = validate_answers({"answers": [row("1002", ["A"], True)]}, [world.questions[1]])
    assert not missing and valid["1002"].uncertain


def test_unknown_question_id_rejected(world):
    with pytest.raises(ProviderError):
        validate_answers({"answers": [row("unknown")]}, world.questions)


async def test_missing_only_subset_requested(harness, world):
    world.bad_ai = [json.dumps({"answers": [row()]})]
    requests = []
    async with harness(world) as (_, _, _, _, ai):
        original = ai._request

        async def record(messages, *args, **kwargs):
            requests.append(json.loads(messages[1]["content"])["questions"])
            return await original(messages, *args, **kwargs)

        ai._request = record
        result = await ai.answer(world.questions, monotonic() + 10, False)
        assert len(result) == 2
        assert [q["question_id"] for q in requests[1]] == ["1002"]


async def test_invalid_option_only_subset_requested(harness, world):
    world.bad_ai = [json.dumps({"answers": [row(answer=["Z"]), row("1002", ["A", "C"])]})]
    requests = []
    async with harness(world) as (_, _, _, _, ai):
        original = ai._request

        async def record(messages, *args, **kwargs):
            requests.append(json.loads(messages[1]["content"])["questions"])
            return await original(messages, *args, **kwargs)

        ai._request = record
        await ai.answer(world.questions, monotonic() + 10, False)
        assert [q["question_id"] for q in requests[1]] == ["1001"]


async def test_uncertain_second_pass_only_subset(harness, world):
    world.bad_ai = [json.dumps({"answers": [row(uncertain=True), row("1002", ["A", "C"])]})]
    async with harness(world) as (_, _, _, _, ai):
        answers = await ai.answer(world.questions, monotonic() + 60)
        assert not any(a.uncertain for a in answers) and world.ai_calls == 2


async def test_uncertain_retained_when_no_time(harness, world):
    world.bad_ai = [json.dumps({"answers": [row(uncertain=True), row("1002", ["A", "C"])]})]
    async with harness(world) as (_, _, _, _, ai):
        answers = await ai.answer(world.questions, monotonic() + 5)
        assert answers[0].uncertain and world.ai_calls == 1


async def test_concurrency_bound(world):
    c = DeepSeek(batch_size=1, max_concurrency=2, max_retries=0)
    active, peak = 0, 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        status, body = world.response("POST", "/chat/completions", json.loads(request.content))
        active -= 1
        return httpx.Response(status, json=body)

    ai = DeepSeekProvider(
        c, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), api_key="TEST"
    )
    try:
        await ai.answer(world.questions, monotonic() + 5, False)
        assert peak == 2
    finally:
        await ai.close()


async def test_bounded_transport_retry_and_deadline(world):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("secret must not be logged")

    ai = DeepSeekProvider(
        DeepSeek(max_retries=0),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key="TEST",
    )
    try:
        with pytest.raises(ProviderError, match="重试已耗尽"):
            await ai._batch(world.questions, monotonic() + 2)
        assert calls == 1
        with pytest.raises(DeadlineError):
            await ai._batch(world.questions, monotonic() - 1)
        assert calls == 1
    finally:
        await ai.close()


async def test_request_has_only_question_data_and_official_params(world):
    captured = []

    async def handler(request):
        captured.append(request)
        code, body = world.response("POST", "/chat/completions", json.loads(request.content))
        return httpx.Response(code, json=body)

    ai = DeepSeekProvider(
        DeepSeek(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), api_key="TEST"
    )
    try:
        await ai.answer(world.questions, monotonic() + 5, False)
        request = captured[0]
        payload = json.loads(request.content)
        assert request.url.host == "api.deepseek.com"
        assert "cookie" not in request.headers
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["response_format"] == {"type": "json_object"}
        assert set(json.loads(payload["messages"][1]["content"])["questions"][0]) == {
            "question_id",
            "type",
            "question",
            "options",
        }
    finally:
        await ai.close()


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
async def test_error_response_never_used_as_answers(world, status):
    async def handler(_):
        return httpx.Response(
            status, json={"secret": "not logged"}, headers={"Location": "https://evil.invalid"}
        )

    ai = DeepSeekProvider(
        DeepSeek(max_retries=0),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key="TEST",
    )
    try:
        with pytest.raises(ProviderError):
            await ai._batch(world.questions, monotonic() + 2)
    finally:
        await ai.close()
