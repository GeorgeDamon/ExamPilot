import asyncio
import logging
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from .config import WEEKDAYS
from .deepseek import validate_answers
from .models import (
    CompatibilityError,
    DeadlineError,
    ExamState,
    NeedsUser,
    RateLimited,
    TaskState,
    utcnow,
)

log = logging.getLogger("autoexam")
TERMINAL = {
    TaskState.SUBMITTED,
    TaskState.DRY_RUN,
    TaskState.NEEDS_USER,
    TaskState.FAILED,
    TaskState.EXPIRED,
}
UNCERTAIN_SUBMIT = {TaskState.SUBMITTING, TaskState.SUBMISSION_UNKNOWN}


def in_window(config, now=None):
    local = (now or utcnow()).astimezone(ZoneInfo(config.app.timezone))
    s = config.schedule
    return (
        local.weekday() == WEEKDAYS[s.weekday]
        and s.start_time <= local.time().replace(tzinfo=None) < s.monitor_end
    )


class Engine:
    def __init__(self, config, platform, provider, store, notifier, *, allow_live=False):
        self.cfg, self.platform, self.provider = config, platform, provider
        self.store, self.notifier, self.allow_live = store, notifier, allow_live
        self.target = config.monitor.target()
        self.stage_start = 0
        self.started = 0
        self.stop_requested = lambda: False

    async def stage(self, exam, name):
        now = monotonic()
        elapsed = now - self.stage_start
        self.store.event(exam, name, elapsed)
        log.info("exam=%s stage=%s elapsed_seconds=%.3f", exam.exam_id, name, elapsed)
        self.stage_start = now

    async def reconcile(self, exam):
        # Read only. Restart or lost response never causes another submit request.
        for attempt in range(3):
            try:
                status = await self.platform.state(exam)
                if status == ExamState.SUBMITTED:
                    self.store.set(exam, TaskState.SUBMITTED, "服务端考试列表确认")
                    await self.stage(exam, "server_confirmed")
                    await self.notifier.emit(
                        "submitted", f"考试 {exam.name}（ID {exam.exam_id}）提交状态：已确认"
                    )
                    return True
                if status == ExamState.EXPIRED:
                    self.store.set(
                        exam, TaskState.SUBMISSION_UNKNOWN, "截止时未取得提交确认，需人工核对"
                    )
                    await self.notifier.emit(
                        "submission_unknown", "考试已截止，提交结果仍待人工核对"
                    )
                    return False
            except RateLimited:
                break
            except Exception:
                # Exception text may carry signed URLs/HTTP headers. Do not log it.
                pass
            if exam.deadline and (exam.deadline - utcnow()).total_seconds() < 2:
                break
            if attempt < 2:
                await asyncio.sleep(1)
        self.store.set(exam, TaskState.SUBMISSION_UNKNOWN, "未获得服务端确认；禁止自动重交")
        await self.notifier.emit(
            "submission_unknown", f"考试 {exam.name} 提交结果未知，请核对；程序不会自动重交"
        )
        return False

    async def handle(self, exam):
        if exam.target != self.target:
            raise CompatibilityError("发现非目标课程考试")
        self.started = self.stage_start = monotonic()
        known = self.store.get(exam)
        if known == TaskState.SUBMITTED:
            return
        if known in UNCERTAIN_SUBMIT:
            # Do not repeatedly emit an unchanged UNKNOWN state on every poll.
            if exam.state == ExamState.SUBMITTED or known == TaskState.SUBMITTING:
                await self.reconcile(exam)
            return
        if exam.state == ExamState.SUBMITTED:
            self.store.set(exam, TaskState.SUBMITTED, "发现时已获服务端已完成状态")
            return
        if known in TERMINAL:
            return
        if exam.state not in {ExamState.AVAILABLE, ExamState.IN_PROGRESS}:
            return
        if exam.starts_at and utcnow() < exam.starts_at:
            return
        if exam.deadline and utcnow() >= exam.deadline:
            self.store.set(exam, TaskState.EXPIRED)
            return
        if not self.cfg.exam.auto_answer:
            return
        self.store.set(exam, TaskState.DISCOVERED)
        await self.stage(exam, "discovered")
        await self.notifier.emit("discovered", f"发现考试：{exam.name}，ID {exam.exam_id}")
        try:
            if self.platform.is_live and not self.cfg.exam.dry_run and not self.allow_live:
                raise NeedsUser("正式运行尚未通过本机模拟及真实读取检查")
            await self.platform.enter(exam)
            if not exam.deadline:
                raise CompatibilityError("服务端截止时间未知，不能可靠自动作答")
            budget = (exam.deadline - utcnow()).total_seconds()
            if budget <= self.cfg.exam.reserve_seconds:
                raise DeadlineError("剩余考试时间不足以作答并预留提交确认")
            async with asyncio.timeout(budget):
                await self._answer_and_submit(exam)
        except Exception as exc:
            current = self.store.get(exam)
            if current in UNCERTAIN_SUBMIT:
                await self.reconcile(exam)
                return
            # TaskGroup errors are exception groups; check leaves without logging raw errors.
            leaves = list(exc.exceptions) if isinstance(exc, ExceptionGroup) else [exc]
            if any(isinstance(x, NeedsUser) for x in leaves):
                state, event = TaskState.NEEDS_USER, "needs_user"
            elif any(isinstance(x, (DeadlineError, TimeoutError)) for x in leaves):
                state, event = TaskState.EXPIRED, "timeout"
            else:
                state, event = TaskState.FAILED, "failed"
            safe_detail = (
                str(exc)
                if isinstance(exc, (NeedsUser, CompatibilityError, DeadlineError))
                else f"阶段失败（{type(exc).__name__}）；无凭据/响应内容"
            )
            self.store.set(exam, state, safe_detail)
            await self.notifier.emit(event, f"考试 {exam.name}：{safe_detail}")

    async def _answer_and_submit(self, exam):
        questions = await self.platform.questions(exam)
        if not questions or len(questions) > self.cfg.exam.max_questions:
            raise CompatibilityError("题目数量为空或超过上限")
        if len({q.question_id for q in questions}) != len(questions):
            raise CompatibilityError("题目 ID 重复")
        if sorted(q.number for q in questions) != list(range(1, len(questions) + 1)):
            raise CompatibilityError("题号缺失或重复")
        if any(q.media for q in questions):
            raise NeedsUser("图片或复杂数学表达式未完整读取，停止自动作答")
        if any(
            (q.type == "single_choice" and not self.cfg.exam.single_choice)
            or (q.type == "multiple_choice" and not self.cfg.exam.multiple_choice)
            for q in questions
        ):
            raise CompatibilityError("试卷含被禁用的题型")
        await self.stage(exam, "questions_extracted")
        self.store.set(exam, TaskState.ANSWERING)
        await self.notifier.emit(
            "answering", f"考试 {exam.name}：开始 AI 作答，共 {len(questions)} 题"
        )
        budget = (exam.deadline - utcnow()).total_seconds() - self.cfg.exam.reserve_seconds
        answers = await self.provider.answer(
            questions, monotonic() + budget, self.cfg.exam.uncertain_second_pass
        )
        valid, missing = validate_answers({"answers": [a.model_dump() for a in answers]}, questions)
        if missing or len(valid) != len(questions):
            raise CompatibilityError("填写前答案完整性校验失败")
        self.store.set(exam, TaskState.READY)
        await self.stage(exam, "ai_answered")
        await self.notifier.emit(
            "answered",
            f"考试 {exam.name}：AI 推测答案完成，未确认正确性；不确定 {sum(a.uncertain for a in answers)} 题",
        )
        if self.cfg.exam.dry_run:
            self.store.set(exam, TaskState.DRY_RUN, "仅本地验证答案，未向正式平台填写/保存/交卷")
            await self.stage(exam, "dry_run_complete")
            return
        if self.stop_requested():
            raise NeedsUser("用户请求停止，未继续填写/交卷")
        if (exam.deadline - utcnow()).total_seconds() <= 2:
            raise DeadlineError("考试已临近截止")
        current = await self.platform.state(exam)
        if current == ExamState.SUBMITTED:
            self.store.set(exam, TaskState.SUBMITTED, "填写前服务端已确认提交")
            return
        if current not in {ExamState.AVAILABLE, ExamState.IN_PROGRESS}:
            raise CompatibilityError("填写前考试状态不明确或已截止")
        self.store.set(exam, TaskState.SAVING)
        await self.platform.save_answers(exam, answers)
        if not await self.platform.verify_saved(exam, answers):
            raise CompatibilityError("服务端/页面答案读回验证不一致")
        await self.stage(exam, "answers_saved_verified")
        if not self.cfg.exam.auto_submit:
            self.store.set(
                exam, TaskState.NEEDS_USER, "答案已保存，auto_submit=false，等待人工提交"
            )
            await self.notifier.emit("needs_user", "答案已保存，请在考试截止前人工提交")
            return
        if self.stop_requested():
            raise NeedsUser("用户请求停止，已保存答案但未交卷")
        if utcnow() >= exam.deadline:
            raise DeadlineError("提交前考试已截止")
        if await self.platform.state(exam) != ExamState.IN_PROGRESS:
            raise CompatibilityError("提交前服务端未确认正在作答")
        self.store.set(exam, TaskState.SUBMITTING, "请求前持久化；中断后只查状态")
        await self.stage(exam, "submit_requested")
        try:
            await self.platform.submit(exam)
        except Exception:
            pass  # The submission might already have reached the server.
        if await self.reconcile(exam):
            await self.notifier.emit(
                "completion",
                f"【学习通考试完成】课程 ID {self.target.course_id}；考试 {exam.name}；题目 {len(questions)}；AI 作答 {len(answers)}；提交状态已确认；总耗时 {monotonic() - self.started:.1f} 秒",
            )

    async def tick(self, *, resume_only=False):
        exams = await self.platform.list_exams()
        # Earliest known deadlines first. Unknown deadlines require entering to retrieve.
        exams.sort(key=lambda e: e.deadline or datetime.max.replace(tzinfo=utcnow().tzinfo))
        for exam in exams:
            if self.stop_requested():
                break
            if resume_only and self.store.get(exam) not in {
                TaskState.ANSWERING,
                TaskState.READY,
                TaskState.SAVING,
                TaskState.SUBMITTING,
                TaskState.SUBMISSION_UNKNOWN,
            }:
                continue
            await self.handle(exam)
        return len(exams)

    async def monitor(self, stop_requested, heartbeat=lambda: None, *, scheduled=False, once=False):
        self.stop_requested = stop_requested
        await self.notifier.emit("monitor_started", "指定课程考试监控启动")
        failures = 0
        try:
            await self.platform.health()
            await self.provider.health()
            first = True
            while not stop_requested():
                heartbeat()
                active_window = in_window(self.cfg)
                if scheduled and not active_window:
                    if self.store.has_unfinished(self.target.key):
                        await self.tick(resume_only=True)
                    break
                # Check existing available exams immediately at startup (including 07:59).
                local = utcnow().astimezone(ZoneInfo(self.cfg.app.timezone))
                if (
                    first
                    and not self.cfg.monitor.check_on_startup
                    and scheduled
                    and local.time().replace(tzinfo=None) < self.cfg.schedule.monitor_start
                ):
                    count = 0
                else:
                    try:
                        count = await self.tick()
                        failures = 0
                    except NeedsUser:
                        await self.notifier.emit(
                            "login_failed", "学习通会话失效或要求核验，请人工登录"
                        )
                        raise NeedsUser("学习通会话失效，暂停定时任务直至人工恢复") from None
                    except RateLimited as exc:
                        failures += 1
                        count = 0
                        delay = exc.seconds
                    except Exception:
                        failures += 1
                        count = 0
                        delay = min(self.cfg.monitor.interval_seconds * 2**failures, 300)
                    if failures >= self.cfg.monitor.max_consecutive_failures:
                        await self.notifier.emit(
                            "monitor_failed", "连续请求失败达到上限，监控已停止"
                        )
                        raise CompatibilityError("连续请求失败达到上限，暂停定时任务")
                first = False
                if once:
                    return count
                if not failures:
                    delay = self.cfg.monitor.interval_seconds
                if scheduled:
                    end = local.replace(
                        hour=self.cfg.schedule.monitor_end.hour,
                        minute=self.cfg.schedule.monitor_end.minute,
                        second=0,
                        microsecond=0,
                    )
                    delay = min(delay, max(0, (end - local).total_seconds()))
                until = monotonic() + delay
                while monotonic() < until and not stop_requested():
                    heartbeat()
                    await asyncio.sleep(min(1, until - monotonic()))
        finally:
            await self.notifier.emit("monitor_ended", "考试监控结束")
