"""Actual Chromium against synthetic local DOM, never a live Chaoxing exam."""

import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from autoexam.live import LivePlatform
from autoexam.models import Answer, CompatibilityError
from autoexam.parsing import parse_questions


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
async def page():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".browser"))
    async with async_playwright() as pw:
        if not Path(pw.chromium.executable_path).exists():
            pytest.skip("需要先从官方安装 Chromium；不能算作浏览器验证通过")
        browser = await pw.chromium.launch(
            headless=True, executable_path=pw.chromium.executable_path
        )
        p = await browser.new_page()
        p.set_default_timeout(2000)
        yield p
        await browser.close()


def dom(typ=0):
    return f'''<html><body><ul><li data="0" onclick="return false">1</li></ul>
    <form id="submitTest">
    <input type="hidden" name="courseId" value="1"><input type="hidden" name="classId" value="2">
    <input type="hidden" name="testPaperId" value="9001"><input type="hidden" id="remainTime" value="300">
    <div class="allAnswerList questionWrap singleQuesId ans-cc-exam">
    <input type="hidden" name="questionId" value="1001"><input type="hidden" name="type1001" value="{typ}">
    <div class="tit"><h3>题型信息</h3>1.模拟题干</div>
    <input type="hidden" id="answer1001" value="B">
    <div class="answerList radioList" name="A" onclick="choose('A')"><cc>2</cc></div>
    <div class="answerList radioList" name="B" onclick="choose('B')"><cc>3</cc></div>
    <div class="answerList radioList" name="C" onclick="choose('C')"><cc>4</cc></div>
    </div></form>
    <button onclick="window.saved=document.getElementById('answer1001').value">保存</button>
    <button onclick="if(confirm('确认提交试卷？'))window.submits=(window.submits||0)+1">交卷</button>
    <script>function choose(key){{
      let el=document.getElementById('answer1001');
      if({typ}===0)el.value=key;
      else {{let x=new Set(el.value); if(x.has(key))x.delete(key);else x.add(key);el.value=[...x].sort().join('');}}
    }}</script></body></html>'''


@pytest.mark.parametrize("typ,answer", [(0, ["A"]), (1, ["A", "C"])])
async def test_normal_dom_clicks_save_and_readback(page, cfg, world, tmp_path, typ, answer):
    await page.set_content(dom(typ))
    platform = LivePlatform(cfg, tmp_path / "runtime")
    platform.exam_page = page
    platform.questions_cache = {q.question_id: q for q in parse_questions(await page.content())}
    a = Answer(question_id="1001", answer=answer, uncertain=False)
    await platform.save_answers(world.exams[0], [a])
    assert set(await page.evaluate("window.saved")) == set(answer)
    assert await platform.verify_saved(world.exams[0], [a])


async def test_browser_dry_run_no_mutation(page, cfg, world, tmp_path):
    cfg.exam.dry_run = True
    await page.set_content(dom())
    platform = LivePlatform(cfg, tmp_path / "runtime")
    platform.exam_page = page
    with pytest.raises(CompatibilityError):
        await platform.save_answers(
            world.exams[0], [Answer(question_id="1001", answer=["A"], uncertain=False)]
        )
    with pytest.raises(CompatibilityError):
        await platform.submit(world.exams[0])
    assert await page.locator("#answer1001").input_value() == "B"
    assert await page.evaluate("window.submits || 0") == 0


async def test_browser_submit_click_once(page, cfg, world, tmp_path):
    await page.set_content(dom())
    platform = LivePlatform(cfg, tmp_path / "runtime")
    platform.exam_page = page
    await platform.submit(world.exams[0])
    assert await page.evaluate("window.submits") == 1


async def test_browser_scope_identity_checked(page, cfg, world, tmp_path):
    await page.set_content(
        dom().replace('name="testPaperId" value="9001"', 'name="testPaperId" value="9999"')
    )
    platform = LivePlatform(cfg, tmp_path / "runtime")
    platform.exam_page = page
    with pytest.raises(CompatibilityError):
        await platform.submit(world.exams[0])
    assert await page.evaluate("window.submits || 0") == 0
