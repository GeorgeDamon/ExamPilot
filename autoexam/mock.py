import json

import httpx

from .models import CompatibilityError, Exam, ExamState, NeedsUser, Question, SubmissionUnknown


class MockPlatform:
    is_live = False

    def __init__(self, url, target, *, client=None):
        self.url, self.target = url, target
        self.client = client or httpx.AsyncClient(timeout=3, trust_env=False)

    async def close(self):
        await self.client.aclose()

    async def _get(self, path, **params):
        r = await self.client.get(self.url + path, params=params)
        if r.status_code == 401:
            raise NeedsUser("模拟登录会话过期")
        r.raise_for_status()
        return r.json()

    async def health(self):
        body = await self._get("/health")
        if body["target"] != self.target.model_dump():
            raise CompatibilityError("模拟目标课程不匹配")

    async def list_exams(self):
        return [Exam.model_validate(e) for e in (await self._get("/exams"))["exams"]]

    async def state(self, exam):
        return ExamState((await self._get("/state", exam_id=exam.exam_id))["state"])

    async def enter(self, exam):
        r = await self.client.post(self.url + "/enter", json={"exam_id": exam.exam_id})
        r.raise_for_status()
        updated = Exam.model_validate(r.json()["exam"])
        exam.deadline, exam.state = updated.deadline, updated.state

    async def questions(self, exam):
        collected, page, seen_pages, expected_total = [], 0, set(), None
        while page is not None:
            if page in seen_pages or len(seen_pages) >= 500:
                raise CompatibilityError("模拟分页循环/超限")
            seen_pages.add(page)
            body = await self._get("/questions", exam_id=exam.exam_id, page=page)
            total = body["total"]
            if expected_total is not None and total != expected_total:
                raise CompatibilityError("分页总题数变化")
            expected_total = total
            collected.extend(Question.model_validate(q) for q in body["questions"])
            page = body["next_page"]
        if len(collected) != expected_total or len({q.question_id for q in collected}) != len(
            collected
        ):
            raise CompatibilityError("模拟分页题数不完整或重复")
        return collected

    async def save_answers(self, exam, answers):
        r = await self.client.post(
            self.url + "/save",
            json={"exam_id": exam.exam_id, "answers": [a.model_dump() for a in answers]},
        )
        r.raise_for_status()

    async def verify_saved(self, exam, answers):
        actual = (await self._get("/saved", exam_id=exam.exam_id))["answers"]
        return len(actual) == len(answers) and all(
            set(actual.get(a.question_id, [])) == set(a.answer) for a in answers
        )

    async def submit(self, exam):
        r = await self.client.post(self.url + "/submit", json={"exam_id": exam.exam_id})
        if r.status_code != 200:
            raise SubmissionUnknown("模拟提交响应异常，须查询真实状态")


def simulated_ai_client(url):
    """Reroute only in test/demo; no real API key, no real network to DeepSeek."""

    async def handler(request):
        async with httpx.AsyncClient(trust_env=False) as c:
            r = await c.post(url + "/chat/completions", json=json.loads(request.content))
            return httpx.Response(r.status_code, json=r.json())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
