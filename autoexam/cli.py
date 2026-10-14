import argparse
import asyncio
import json
import os
import plistlib
import shutil
import sys
import uuid
from pathlib import Path

from .config import load_config
from .deepseek import DeepSeekProvider
from .engine import Engine, in_window
from .live import LivePlatform
from .mock import MockPlatform, simulated_ai_client
from .mock_server import MockServer, World
from .models import NeedsUser, TaskState, utcnow
from .notifications import Notifier
from .runtime import InstanceLock, code_hash, load_env, running, setup_logging, validate_receipt
from .store import Store, private_dir, private_json

PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parent
ROOT = Path(
    os.environ.get(
        "EXAMPILOT_HOME",
        str(SOURCE_ROOT if (SOURCE_ROOT / "pyproject.toml").exists() else Path.cwd()),
    )
).resolve()


async def demo(cfg, runtime, *, lost_response=False):
    cfg = cfg.model_copy(deep=True)
    if not cfg.monitor.target_course_id:
        cfg.monitor.target_course_id, cfg.monitor.target_class_id, cfg.monitor.target_cpi = (
            "1",
            "2",
            "3",
        )
    cfg.exam.dry_run = False  # Only for the isolated synthetic server.
    cfg.platform.backend = "mock"
    cfg.notification.enabled = False
    cfg.deepseek.max_retries = 0
    world = World(cfg.monitor.target())
    world.lost_submit_response = lost_response
    with MockServer(world) as server:
        platform = MockPlatform(server.url, cfg.monitor.target())
        provider = DeepSeekProvider(
            cfg.deepseek, client=simulated_ai_client(server.url), api_key="LOCAL-MOCK-NOT-A-SECRET"
        )
        store = Store(runtime / f"demo-{uuid.uuid4().hex[:12]}.sqlite3")
        notifier = Notifier(cfg.notification)
        try:
            engine = Engine(cfg, platform, provider, store, notifier)
            await engine.tick()
            await engine.tick()  # Prove duplicate detection does not submit again.
            result = {
                "mock_chain_passed": store.get(world.exams[0]) == TaskState.SUBMITTED,
                "submit_calls": world.submit_calls,
                "ai_calls": world.ai_calls,
                "events": notifier.events,
                "live_platform_tested": False,
            }
            if not result["mock_chain_passed"] or world.submit_calls != 1:
                raise RuntimeError("模拟全链路/去重检查失败")
            private_json(
                runtime / "mock-validation.json",
                {**result, "code_sha256": code_hash(ROOT), "at": utcnow().isoformat()},
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result
        finally:
            await provider.close()
            await platform.close()
            store.close()


async def live_command(args, cfg, runtime, lock):
    platform = LivePlatform(cfg, runtime)
    provider = None
    try:
        await platform.open(login=args.command == "login")
        if args.command == "login":
            await platform.login()
            await platform.health()
            print("登录会话已保存在本机（600 权限）；目标课程定位检查通过。")
            return
        if args.command in {"probe", "list"}:
            await platform.health()
            exams = await platform.list_exams()
            print(
                json.dumps([e.model_dump(mode="json") for e in exams], ensure_ascii=False, indent=2)
            )
            if args.command == "list" or not args.exam_id:
                print("考试列表读取通过；未进入新考试，未验证真实题目或提交。")
                return
            exam = next((e for e in exams if e.exam_id == args.exam_id), None)
            if exam is None or exam.state.value != "IN_PROGRESS":
                raise NeedsUser("真实读取探测要求一场已经由你人工进入的练习考试；不会自动开启考试")
            cfg.exam.dry_run = True
            await platform.enter(exam)
            questions = await platform.questions(exam)
            if any(q.media for q in questions):
                raise NeedsUser("试卷含无法读取的图片/公式，不能通过完整读取检查")
            mock = await demo(cfg, runtime / "probe-mock")
            private_json(
                runtime / "validation.json",
                {
                    "target": cfg.monitor.target().model_dump(),
                    "code_sha256": code_hash(ROOT),
                    "mock_chain_passed": mock["mock_chain_passed"],
                    "live_read_passed": True,
                    "exam_id": exam.exam_id,
                    "question_count": len(questions),
                    "live_submit_verified": False,
                    "at": utcnow().isoformat(),
                },
            )
            print(f"完整读取 {len(questions)} 题，未填写/保存/交卷。真实提交仍需受监督验证。")
            return
        provider = DeepSeekProvider(cfg.deepseek)
        notifier = Notifier(cfg.notification)
        store = Store(runtime / "state.sqlite3")
        try:
            live_ready = validate_receipt(ROOT, runtime, cfg.monitor.target())
            if not cfg.exam.dry_run and not live_ready:
                raise NeedsUser("缺少与当前代码和目标课程绑定的真实读取检查记录；保留 dry_run=true")
            engine = Engine(cfg, platform, provider, store, notifier, allow_live=live_ready)
            await engine.monitor(
                lock.stop_requested,
                lock.heartbeat,
                scheduled=args.command == "scheduled",
                once=args.once,
            )
        finally:
            store.close()
    finally:
        if provider:
            await provider.close()
        await platform.close()


def write_launchd(cfg, destination):
    if not (ROOT / "scripts" / "launch.sh").exists():
        raise NeedsUser("launchd 安装请使用完整源码仓库，wheel 仅提供命令行功能")
    # StartCalendarInterval is in OS local time. StartInterval + app timezone gate
    # handles OS timezone changes and restart recovery without silently shifting the window.
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from .config import WEEKDAYS

    now = utcnow().astimezone(ZoneInfo(cfg.app.timezone))
    next_run = now + timedelta(days=(WEEKDAYS[cfg.schedule.weekday] - now.weekday()) % 7)
    next_run = next_run.replace(
        hour=cfg.schedule.start_time.hour,
        minute=cfg.schedule.start_time.minute,
        second=0,
        microsecond=0,
    )
    local = next_run.astimezone()
    runtime = (ROOT / cfg.platform.runtime_dir).resolve()
    plist = {
        "Label": "com.local.exampilot",
        "ProgramArguments": [str(ROOT / "scripts" / "launch.sh")],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "StartInterval": 60,
        "StartCalendarInterval": {
            "Weekday": (local.weekday() + 1) % 7,
            "Hour": local.hour,
            "Minute": local.minute,
        },
        "StandardOutPath": str(runtime / "launchd.stdout.log"),
        "StandardErrorPath": str(runtime / "launchd.stderr.log"),
        "ProcessType": "Background",
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    private_dir(runtime)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(plistlib.dumps(plist))
    destination.chmod(0o600)
    print(f"launchd 配置已生成：{destination}")


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description="学习通指定课程考试监控；默认只读")
    p.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    p.add_argument(
        "command",
        choices=[
            "init",
            "demo",
            "login",
            "list",
            "probe",
            "ai-test",
            "run",
            "scheduled",
            "status",
            "stop",
            "retry",
            "plist",
        ],
    )
    p.add_argument("--once", action="store_true")
    p.add_argument("--exam-id")
    p.add_argument("--lost-response", action="store_true")
    p.add_argument("--output", type=Path, default=ROOT / "launchd" / "com.local.exampilot.plist")
    args = p.parse_args()
    try:
        if args.command == "init":
            private_dir(ROOT)
            for source, destination in [
                ("config.example.yaml", "config.yaml"),
                ("env.example", ".env"),
            ]:
                path = ROOT / destination
                if not path.exists():
                    shutil.copyfile(PACKAGE_ROOT / "resources" / source, path)
                    path.chmod(0o600)
            print("ExamPilot 本机配置已初始化；已有配置不会被覆盖。")
            return 0
        if args.config.exists():
            config_path = args.config
        elif args.config != ROOT / "config.yaml":
            raise NeedsUser("指定的配置文件不存在；不会静默改用其他配置")
        else:
            config_path = ROOT / "config.example.yaml"
            if not config_path.exists():
                config_path = PACKAGE_ROOT / "resources" / "config.example.yaml"
        cfg = load_config(config_path)
        load_env(ROOT / ".env")
        runtime = private_dir((ROOT / cfg.platform.runtime_dir).resolve())
        setup_logging(runtime, cfg.logging.level)
        if args.command == "demo":
            asyncio.run(demo(cfg, runtime / "demo", lost_response=args.lost_response))
        elif args.command == "ai-test":

            async def test():
                provider = DeepSeekProvider(cfg.deepseek)
                try:
                    await provider.health()
                    print("DeepSeek 实际 API、模型和 JSON 参数探测通过；消耗少量 API token。")
                finally:
                    await provider.close()

            asyncio.run(test())
        elif args.command == "plist":
            write_launchd(cfg, args.output)
        elif args.command == "status":
            store = Store(runtime / "state.sqlite3")
            try:
                print(
                    json.dumps(
                        {
                            "running": running(runtime),
                            "dry_run": cfg.exam.dry_run,
                            "schedule_window_now": in_window(cfg),
                            "live_ready": validate_receipt(ROOT, runtime, cfg.monitor.target()),
                            "tasks": store.rows(),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            finally:
                store.close()
        elif args.command == "stop":
            private_json(
                runtime / "attention.required",
                {"reason": "manual_stop", "at": utcnow().isoformat()},
            )
            if running(runtime):
                (runtime / "stop.request").touch(mode=0o600)
                print("已请求停止。当前考试操作将于有界调用结束后退出；不会强杀或自动交卷。")
            else:
                print("当前没有监控实例。")
        elif args.command == "retry":
            if running(runtime):
                raise RuntimeError("重试前请停止监控")
            if not args.exam_id:
                raise ValueError("retry 必须提供 --exam-id")
            store = Store(runtime / "state.sqlite3")
            try:
                with store.db:
                    store.db.execute(
                        "DELETE FROM tasks WHERE scope=? AND exam_id=? AND state IN (?,?,?)",
                        (
                            cfg.monitor.target().key,
                            args.exam_id,
                            TaskState.FAILED,
                            TaskState.NEEDS_USER,
                            TaskState.DRY_RUN,
                        ),
                    )
                print("可重试失败/人工处理/模拟任务已重置；已提交或结果未知任务不会被重置。")
            finally:
                store.close()
        else:
            if args.command == "scheduled":
                if (runtime / "attention.required").exists():
                    return
                store = Store(runtime / "state.sqlite3")
                unfinished = store.has_unfinished(cfg.monitor.target().key)
                store.close()
                if not in_window(cfg) and not unfinished:
                    return
                if running(runtime):
                    return
            with InstanceLock(runtime) as lock:
                if cfg.platform.backend == "mock" and args.command in {"run", "scheduled"}:
                    asyncio.run(demo(cfg, runtime / "demo"))
                    return 0
                if args.command in {"run", "login"}:
                    (runtime / "attention.required").unlink(missing_ok=True)
                try:
                    asyncio.run(live_command(args, cfg, runtime, lock))
                except Exception:
                    if args.command in {"run", "scheduled"}:
                        private_json(
                            runtime / "attention.required",
                            {"reason": "startup_or_service_failure", "at": utcnow().isoformat()},
                        )
                        asyncio.run(
                            Notifier(cfg.notification).emit(
                                "startup_failed",
                                "监控启动/运行失败，定时任务已暂停；修复配置后手动 start 恢复。",
                            )
                        )
                    raise
    except (KeyboardInterrupt, EOFError):
        print("操作已停止。")
        return 130
    except Exception as exc:
        from .models import AutoExamError

        safe = (
            str(exc)
            if isinstance(exc, AutoExamError)
            else f"操作失败（{type(exc).__name__}）；未输出敏感详情"
        )
        print(safe, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
