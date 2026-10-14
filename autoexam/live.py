"""Normal browser UI adapter. Never synthesize security checks or submit signatures.

PC list structure has live evidence. Active question/save/submit flow remains experimental
until a local supervised practice exam verifies it. Unknown structures fail closed.
"""

import asyncio
import os
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .models import CompatibilityError, ExamState, NeedsUser, RateLimited, utcnow
from .parsing import COURSE_HOME, guard_html, parse_exam_list, parse_questions, safe_url
from .store import private_dir, private_json


class LivePlatform:
    is_live = True

    def __init__(self, config, runtime: Path):
        self.config = config
        self.target = config.monitor.target()
        self.runtime = private_dir(runtime)
        self.context = self.browser = self.pw = None
        self.page = None
        self.list_frame = None
        self.exam_page = None
        self.questions_cache = {}
        self.card_indices = []

    async def open(self, *, login=False):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(self.runtime.parent / ".browser"))
        from playwright.async_api import async_playwright

        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(
            headless=False if login else self.config.platform.headless,
            executable_path=self.pw.chromium.executable_path,
        )
        state = self.runtime / "session.json"
        self.context = await self.browser.new_context(
            storage_state=state if state.exists() else None
        )
        self.context.set_default_timeout(self.config.platform.timeout_seconds * 1000)
        self.page = await self.context.new_page()

    async def close(self):
        if self.context:
            await self.persist()
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()

    async def persist(self):
        if self.context:
            private_json(self.runtime / "session.json", await self.context.storage_state())

    async def _goto(self, page, url):
        r = await page.goto(safe_url(url), wait_until="domcontentloaded")
        if r and r.status == 429:
            raise RateLimited(r.headers.get("retry-after", 60))
        if r and r.status >= 400:
            raise CompatibilityError(f"学习通 HTTP {r.status}")
        safe_url(page.url)
        guard_html(await page.content(), page.url)

    async def login(self):
        await self.page.goto("https://passport2.chaoxing.com/login", wait_until="domcontentloaded")
        print("请在弹出的浏览器正常登录（含协议和验证）；本程序不保存密码。完成后回到终端按回车。")
        await asyncio.to_thread(input)
        guard_html(await self.page.content(), self.page.url)
        await self.persist()

    async def health(self):
        if not (self.runtime / "session.json").exists():
            raise NeedsUser("本项目尚无登录会话，请先运行 login")
        await self._open_course()
        await self.list_exams()

    async def _open_course(self):
        # Optional signed URL is PRIVATE and never appears in logs or public config.
        url = os.environ.get("AUTOEXAM_COURSE_URL")
        if url:
            p = parse_qs(urlsplit(safe_url(url)).query)
            if (
                p.get("courseid") != [self.target.course_id]
                or p.get("clazzid") != [self.target.class_id]
                or p.get("cpi") != [self.target.cpi]
            ):
                raise CompatibilityError("本机课程 URL 与目标 ID 不一致")
            await self._goto(self.page, url)
        else:
            await self._goto(self.page, COURSE_HOME)
            links = self.page.locator("a[href*='courseid=']")
            found = []
            for i in range(await links.count()):
                href = await links.nth(i).get_attribute("href")
                params = parse_qs(urlsplit(href or "").query)
                if params.get("courseid") == [self.target.course_id] and params.get("clazzid") == [
                    self.target.class_id
                ]:
                    found.append(href)
            if len(found) != 1:
                raise CompatibilityError(
                    "不能唯一定位课程；请将完整课程 URL 填入本机 .env 的 AUTOEXAM_COURSE_URL"
                )
            await self._goto(self.page, safe_url(found[0], self.page.url))
        exam_link = self.page.get_by_role("link", name="考试", exact=True)
        if await exam_link.count() != 1:
            raise CompatibilityError("指定课程没有唯一的考试入口")
        await exam_link.click()
        frame_node = self.page.locator("iframe#frame_content-ks")
        await frame_node.wait_for(state="attached")
        src = await frame_node.get_attribute("src")
        src = safe_url(src or "", self.page.url)
        params = parse_qs(urlsplit(src).query)
        if (
            params.get("courseid") != [self.target.course_id]
            or params.get("clazzid") != [self.target.class_id]
            or params.get("cpi") != [self.target.cpi]
        ):
            raise CompatibilityError("考试框架课程 ID 校验失败")
        self.list_frame = await frame_node.element_handle()
        self.list_frame = await self.list_frame.content_frame()
        await self.list_frame.wait_for_load_state("domcontentloaded")

    async def list_exams(self):
        if self.list_frame is None:
            await self._open_course()
        else:
            # Refresh only the list iframe. Retains the active exam page separately.
            await self.list_frame.goto(safe_url(self.list_frame.url), wait_until="domcontentloaded")
        html = await self.list_frame.content()
        return parse_exam_list(html, self.target, self.list_frame.url)

    async def state(self, exam):
        for e in await self.list_exams():
            if e.exam_id == exam.exam_id:
                return e.state
        return ExamState.UNKNOWN

    async def _click_exact(self, page, labels):
        found = []
        for label in labels:
            for role in ("button", "link"):
                loc = page.get_by_role(role, name=label, exact=True)
                for i in range(await loc.count()):
                    node = loc.nth(i)
                    if await node.is_visible() and await node.is_enabled():
                        found.append(node)
        if len(found) != 1:
            raise CompatibilityError("没有唯一可用的操作按钮，需实际页面适配")
        await found[0].click()

    async def enter(self, exam):
        if exam.target != self.target:
            raise CompatibilityError("非目标课程")
        if self.config.exam.dry_run and exam.state != ExamState.IN_PROGRESS:
            raise NeedsUser("只读模式不会开启考试倒计时；请在受监督的练习考试中人工进入")
        rows = self.list_frame.locator("li").filter(has_text=exam.name)
        match = []
        for i in range(await rows.count()):
            handler = await rows.nth(i).locator("[onclick]").get_attribute("onclick")
            if re.search(r"\(['\"]" + re.escape(exam.exam_id) + r"['\"]", handler or ""):
                match.append(rows.nth(i))
        if len(match) != 1:
            raise CompatibilityError("无法唯一匹配考试 ID")
        old_pages = set(self.context.pages)
        await match[0].locator("[onclick]").click()
        await self.page.wait_for_timeout(300)
        new = [p for p in self.context.pages if p not in old_pages]
        self.exam_page = new[0] if len(new) == 1 else self.page
        await self.exam_page.wait_for_load_state("domcontentloaded")
        guard_html(await self.exam_page.content(), self.exam_page.url)
        if not await self.exam_page.locator("form#submitTest").count():
            if self.config.exam.dry_run:
                raise CompatibilityError("只读模式未进入可读取试卷，不点击开始按钮")
            # Never accept an exam agreement or safety verification automatically.
            text = await self.exam_page.locator("body").inner_text()
            if any(x in text for x in ["承诺书", "诚信承诺", "人脸", "验证码", "考试客户端"]):
                raise NeedsUser("考试入口要求协议/身份核验/专用客户端")
            await self._click_exact(self.exam_page, self.config.platform.start_labels)
            await self.exam_page.wait_for_load_state("domcontentloaded")
        await self._deadline(exam)

    async def _deadline(self, exam):
        soup = guard_html(await self.exam_page.content(), self.exam_page.url)
        form = soup.select_one("form#submitTest")
        if form is None:
            raise CompatibilityError("当前试卷没有已知 submitTest 表单")
        for key, expected in [
            ("courseId", self.target.course_id),
            ("classId", self.target.class_id),
            ("testPaperId", exam.exam_id),
        ]:
            node = form.select_one(f"input[name='{key}']")
            if node is None or node.get("value") != expected:
                raise CompatibilityError("试卷身份字段缺失或不匹配")
        remain = form.select_one("input#remainTime")
        if remain is None or not remain.get("value", "").isdigit():
            raise CompatibilityError("无法读取服务端剩余考试时间")
        seconds = int(remain["value"])
        deadline = utcnow() + timedelta(seconds=seconds)
        exam.deadline = min(exam.deadline, deadline) if exam.deadline else deadline
        exam.state = ExamState.IN_PROGRESS

    async def questions(self, exam):
        await self._deadline(exam)
        soup = guard_html(await self.exam_page.content())
        # Source-backed answer-card structure. Cannot declare completeness from rows alone.
        indices = sorted(
            {int(n["data"]) for n in soup.select("ul li[data]") if n["data"].isdigit()}
        )
        if not indices or indices != list(range(len(indices))):
            raise CompatibilityError("答题卡总数/连续索引缺失，无法证明未遗漏题目")
        if len(indices) > self.config.exam.max_questions:
            raise CompatibilityError("题目数量超过配置上限")
        self.card_indices = indices
        collected = {}
        for index in indices:
            loc = self.exam_page.locator(f"ul li[data='{index}']")
            if await loc.count() != 1:
                raise CompatibilityError("分页导航不唯一")
            await loc.click()
            await self.exam_page.wait_for_load_state("domcontentloaded")
            qs = parse_questions(await self.exam_page.content(), first_number=index + 1)
            for q in qs:
                existing = collected.get(q.question_id)
                if existing and existing.model_dump(exclude={"existing_answer"}) != q.model_dump(
                    exclude={"existing_answer"}
                ):
                    raise CompatibilityError("分页中的同一题目内容变化")
                collected[q.question_id] = q
        if len(collected) != len(indices) or sorted(q.number for q in collected.values()) != list(
            range(1, len(indices) + 1)
        ):
            raise CompatibilityError("分页题数/题号不完整")
        self.questions_cache = collected
        return sorted(collected.values(), key=lambda q: q.number)

    async def save_answers(self, exam, answers):
        if self.config.exam.dry_run:
            raise CompatibilityError("dry_run 禁止向正式平台填写或保存答案")
        await self._deadline(exam)
        for a in answers:
            q = self.questions_cache[a.question_id]
            await self.exam_page.locator(f"ul li[data='{q.number - 1}']").click()
            await self.exam_page.wait_for_load_state("domcontentloaded")
            qs = parse_questions(await self.exam_page.content(), first_number=q.number)
            current = next((x for x in qs if x.question_id == a.question_id), None)
            if current is None or current.model_dump(exclude={"existing_answer"}) != q.model_dump(
                exclude={"existing_answer"}
            ):
                raise CompatibilityError("填写前题目 ID/选项发生变化")
            wrap = self.exam_page.locator("div.questionWrap.singleQuesId.ans-cc-exam").filter(
                has=self.exam_page.locator(f"input[name='questionId'][value='{q.question_id}']")
            )
            if q.type == "single_choice":
                if set(current.existing_answer) != set(a.answer):
                    await wrap.locator(f"div.answerList.radioList[name='{a.answer[0]}']").click()
            else:
                for option in q.options:
                    should, was = option in a.answer, option in current.existing_answer
                    if should != was:
                        await wrap.locator(f"div.answerList.radioList[name='{option}']").click()
            actual = await wrap.locator("input[id^='answer']").input_value()
            if set(actual) != set(a.answer):
                raise CompatibilityError("页面填写结果不匹配")
            # Normal UI save; never replay signed requests from source projects.
            await self._click_exact(self.exam_page, ["保存", "保存答案"])

    async def verify_saved(self, exam, answers):
        qs = await self.questions(exam)
        actual = {q.question_id: set(q.existing_answer) for q in qs}
        return len(actual) == len(answers) and all(
            actual.get(a.question_id) == set(a.answer) for a in answers
        )

    async def submit(self, exam):
        if self.config.exam.dry_run:
            raise CompatibilityError("dry_run 禁止正式交卷")
        await self._deadline(exam)

        # This routine has no retry. Any error is handled by read-only reconciliation.
        async def dialog_handler(dialog):
            if dialog.type == "confirm" and any(x in dialog.message for x in ["交卷", "提交试卷"]):
                await dialog.accept()
            else:
                await dialog.dismiss()

        self.exam_page.on("dialog", dialog_handler)
        try:
            await self._click_exact(self.exam_page, self.config.platform.submit_labels)
            visible = self.exam_page.get_by_role("dialog")
            if await visible.count() == 1 and await visible.is_visible():
                if not any(x in await visible.inner_text() for x in ["交卷", "提交试卷"]):
                    raise NeedsUser("未知提交确认对话框")
                await self._click_exact(visible, self.config.platform.confirm_labels)
        finally:
            self.exam_page.remove_listener("dialog", dialog_handler)
