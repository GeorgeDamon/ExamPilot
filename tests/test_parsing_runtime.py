import json
import os
import plistlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoexam.cli import write_launchd
from autoexam.config import Config, DeepSeek
from autoexam.engine import in_window
from autoexam.models import CompatibilityError, ExamState, NeedsUser, TaskState
from autoexam.parsing import guard_html, parse_exam_list, parse_questions, safe_url
from autoexam.runtime import InstanceLock, code_hash, load_env, running, validate_receipt
from autoexam.store import Store, private_json


def qhtml(qid="1001", typ="0", options=None):
    options = (
        options
        or '<div class="answerList radioList" name="A"><cc>计划<br>组织</cc></div><div class="answerList radioList" name="B"><cc>天气</cc></div>'
    )
    return f'''<div class="allAnswerList questionWrap singleQuesId ans-cc-exam">
    <input name="questionId" value="{qid}"><input name="type{qid}" value="{typ}">
    <div class="tit"><h3>单选题（5分）</h3>1.以下哪个属于管理职能？<p>请阅读全部选项。</p></div>
    <input id="answer{qid}" value="A">{options}</div>'''


def test_pc_list_fixture_based_on_observed_structure(cfg):
    html = (Path(__file__).parent / "fixtures/live_list_sanitized.html").read_text()
    exams = parse_exam_list(
        html, cfg.monitor.target(), "https://mooc1.chaoxing.com/exam-ans/mooc2/exam/exam-list"
    )
    assert [(e.exam_id, e.state) for e in exams] == [
        ("9001", ExamState.SUBMITTED),
        ("9002", ExamState.EXPIRED),
    ]


def test_mobile_source_backed_list(cfg):
    html = '<ul class="nav"><li data="/exam?taskrefId=123&amp;courseId=1&amp;classId=2&amp;cpi=3"><p>test</p><span>未交</span></li></ul>'
    exams = parse_exam_list(html, cfg.monitor.target(), "https://mooc1-api.chaoxing.com")
    assert exams[0].state == ExamState.AVAILABLE


def test_wrong_course_in_entry_rejected(cfg):
    html = '<ul class="nav"><li data="/exam?taskrefId=123&amp;courseId=999"><p>test</p><span>未交</span></li></ul>'
    with pytest.raises(CompatibilityError):
        parse_exam_list(html, cfg.monitor.target(), "https://mooc1-api.chaoxing.com")


def test_empty_known_vs_unknown_html(cfg):
    assert (
        parse_exam_list("<p>暂无考试</p>", cfg.monitor.target(), "https://mooc1.chaoxing.com") == []
    )
    with pytest.raises(CompatibilityError):
        parse_exam_list("<p>加载异常</p>", cfg.monitor.target(), "https://mooc1.chaoxing.com")


@pytest.mark.parametrize(
    "html",
    [
        '<input type="password">',
        '<input id="captcha">',
        '<input id="faceRecognitionCompare" value="1">',
        "<script>var needcode=1;</script>",
    ],
)
def test_auth_challenges_stop(html):
    with pytest.raises(NeedsUser):
        guard_html(html)


def test_text_newlines_unicode_preserved():
    q = parse_questions(qhtml())[0]
    assert "以下哪个" in q.question and "请阅读全部选项" in q.question
    assert q.options == {"A": "计划\n组织", "B": "天气"}
    assert q.existing_answer == ["A"] and q.number == 1


def test_duplicate_option_rejected():
    options = '<div class="answerList radioList" name="A"><cc>1</cc></div>' * 2
    with pytest.raises(CompatibilityError):
        parse_questions(qhtml(options=options))


def test_abnormal_option_id_rejected():
    with pytest.raises(CompatibilityError):
        parse_questions(qhtml().replace('name="B"', 'name="1"'))


def test_image_retained_not_pretended_understood():
    q = parse_questions(qhtml().replace("以下哪个", '<img src="/image.png">以下哪个'))[0]
    assert q.media == ["/image.png"]


def test_math_markup_is_unread_media():
    q = parse_questions(qhtml().replace("以下哪个", "<math>x</math>以下哪个"))[0]
    assert q.media == ["unparsed-math-or-vector"]


def test_unsupported_question_blocks_completeness():
    with pytest.raises(CompatibilityError):
        parse_questions(qhtml(typ="2"))


def test_duplicate_qid_blocks():
    with pytest.raises(CompatibilityError):
        parse_questions(qhtml() + qhtml())


@pytest.mark.parametrize(
    "url",
    [
        "http://mooc1.chaoxing.com",
        "https://chaoxing.com.evil.invalid",
        "https://evilchaoxing.com",
        "javascript:alert(1)",
        "https://user:password@mooc1.chaoxing.com",
    ],
)
def test_platform_url_scope(url):
    with pytest.raises(CompatibilityError):
        safe_url(url)


def test_config_rejects_unofficial_api_and_typos():
    with pytest.raises(ValidationError):
        DeepSeek(base_url="https://proxy.invalid")
    with pytest.raises(ValidationError):
        Config.model_validate({"monitor": {"interval_second": 1}})
    with pytest.raises(ValidationError):
        Config.model_validate({"monitor": {"interval_seconds": 1}})


@pytest.mark.parametrize(
    "hour,minute,expected",
    [(7, 58, False), (7, 59, True), (8, 0, True), (9, 59, True), (10, 0, False)],
)
def test_shanghai_schedule_boundaries(cfg, hour, minute, expected):
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 14, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert in_window(cfg, now) is expected
    assert in_window(cfg, now.astimezone(UTC)) is expected


def test_wrong_weekday_no_dispatch(cfg):
    from zoneinfo import ZoneInfo

    assert not in_window(cfg, datetime(2026, 10, 15, 8, tzinfo=ZoneInfo("Asia/Shanghai")))


def test_single_instance_stale_lock_and_stop_file(tmp_path):
    runtime = tmp_path / "run"
    assert not running(runtime)
    with InstanceLock(runtime) as lock:
        assert running(runtime)
        with pytest.raises(RuntimeError), InstanceLock(runtime):
            pass
        (runtime / "stop.request").touch()
        assert lock.stop_requested()
    assert not running(runtime)
    with InstanceLock(runtime) as lock:
        assert not lock.stop_requested()


def test_persistence_reboot_like_new_process(world, tmp_path):
    path = tmp_path / "state.sqlite3"
    store = Store(path)
    store.set(world.exams[0], TaskState.SUBMITTED)
    store.close()
    reopened = Store(path)
    assert reopened.get(world.exams[0]) == TaskState.SUBMITTED
    with pytest.raises(ValueError):
        reopened.set(world.exams[0], TaskState.DISCOVERED)
    reopened.close()
    assert path.stat().st_mode & 0o777 == 0o600


def test_env_not_shell_executed(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY=$(touch /tmp/SHOULD_NEVER_BE_EXECUTED)\n")
    path.chmod(0o600)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    load_env(path)
    assert os.environ["DEEPSEEK_API_KEY"].startswith("$(touch")
    path.chmod(0o644)
    with pytest.raises(ValueError):
        load_env(path)


def test_receipt_bound_to_code_and_course(tmp_path, cfg):
    root, runtime = tmp_path / "project", tmp_path / "run"
    (root / "autoexam").mkdir(parents=True)
    source = root / "autoexam/a.py"
    source.write_text("a=1")
    receipt = {
        "code_sha256": code_hash(root),
        "target": cfg.monitor.target().model_dump(),
        "mock_chain_passed": True,
        "live_read_passed": True,
    }
    private_json(runtime / "validation.json", receipt)
    assert validate_receipt(root, runtime, cfg.monitor.target())
    source.write_text("a=2")
    assert not validate_receipt(root, runtime, cfg.monitor.target())


def test_launchd_plist_parseable(cfg, tmp_path):
    path = tmp_path / "agent.plist"
    write_launchd(cfg, path)
    data = plistlib.loads(path.read_bytes())
    assert data["StartInterval"] == 60 and data["RunAtLoad"]
    assert data["StartCalendarInterval"]["Minute"] == 59
    assert data["ProgramArguments"][0].endswith("scripts/launch.sh")


def test_no_question_bank_in_state_db(world, tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    store.set(world.exams[0], TaskState.READY)
    tables = [r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert set(tables) == {"tasks", "events"}
    assert "question" not in json.dumps(store.rows())
    store.close()


def test_mock_backend_run_never_opens_live_platform(cfg, tmp_path, monkeypatch):
    import sys

    import yaml

    from autoexam import cli

    cfg.platform.backend = "mock"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["autoexam", "--config", str(path), "run"])
    called = []

    async def fake_demo(*args, **kwargs):
        called.append("mock")

    async def forbidden_live(*args, **kwargs):
        raise AssertionError("mock configuration must not contact the live platform")

    monkeypatch.setattr(cli, "demo", fake_demo)
    monkeypatch.setattr(cli, "live_command", forbidden_live)
    assert cli.main() == 0
    assert called == ["mock"]
