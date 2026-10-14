import re
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .models import CompatibilityError, Exam, ExamState, NeedsUser, Question


LIST_MOBILE = "https://mooc1-api.chaoxing.com/exam/phone/task-list"
COURSE_HOME = "https://mooc2-ans.chaoxing.com/visit/interaction"
STATES = {
    "未发布": ExamState.UNPUBLISHED,
    "未开始": ExamState.NOT_STARTED,
    "尚未开始": ExamState.NOT_STARTED,
    "未交": ExamState.AVAILABLE,
    "待做": ExamState.AVAILABLE,
    "待考试": ExamState.AVAILABLE,
    "可以参加": ExamState.AVAILABLE,
    "正在作答": ExamState.IN_PROGRESS,
    "考试中": ExamState.IN_PROGRESS,
    "进行中": ExamState.IN_PROGRESS,
    "已完成": ExamState.SUBMITTED,
    "已提交": ExamState.SUBMITTED,
    "已过期": ExamState.EXPIRED,
    "已截止": ExamState.EXPIRED,
}


def safe_url(url, base="https://mooc1.chaoxing.com"):
    resolved = urljoin(base, url)
    p = urlsplit(resolved)
    if p.scheme != "https" or not p.hostname or not p.hostname.endswith(".chaoxing.com"):
        raise CompatibilityError("平台 URL 不属于超星 HTTPS 域名")
    if p.username or p.password:
        raise CompatibilityError("URL 禁止包含登录凭据")
    return resolved


def guard_html(html, url=""):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    if "passport2.chaoxing.com/login" in url or soup.select_one("input#pwd, input[type=password]"):
        raise NeedsUser("学习通会话已失效，请在本机重新登录")
    for selector in ("#captcha", "#verifyCode", "#faceRecognition", ".faceVerify"):
        if soup.select_one(selector):
            raise NeedsUser("需要验证码或人脸核验，请人工处理")
    face = soup.select_one("input#faceRecognitionCompare")
    if face and face.get("value", "") not in {"", "0", "false"}:
        raise NeedsUser("考试要求人脸核验")
    if re.search(r"needcode\s*=\s*[1-9]", html):
        raise NeedsUser("考试要求考试码/验证码")
    if any(x in text for x in ["请完成人脸", "请进行人脸", "请完成验证", "访问过于频繁"]):
        raise NeedsUser("平台要求人工核验或限制访问")
    if any(x in text for x in ["无权限访问", "无效的参数", "无效参数", "没有此页面访问权限"]):
        raise CompatibilityError("平台拒绝访问或参数失效")
    return soup


def _date(text, label):
    m = re.search(label + r"\s*[:：]?\s*(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(?::\d{2})?)", text)
    if m:
        return datetime.fromisoformat(m[1].replace("/", "-")).replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
        )
    return None


def parse_exam_list(html, target, base):
    soup = guard_html(html, base)
    exams = []
    # PC selector verified on the user's course on 2026-10-14.
    rows = [li for li in soup.select("ul li") if li.select_one(".right-content p.status")]
    mobile = False
    if not rows:
        nav = soup.select_one("ul.nav")
        if nav is not None:
            rows, mobile = nav.select("li[data]"), True
        elif soup.select_one(".right-content"):
            raise CompatibilityError("电脑端考试行结构变化")
        elif not any(x in soup.get_text() for x in ["暂无考试", "暂无数据", "没有考试"]):
            raise CompatibilityError("无法区分空考试列表与未知页面")
    for row in rows:
        if mobile:
            entry_url = safe_url(row["data"], base)
            params = parse_qs(urlsplit(entry_url).query)
            eid = params.get("taskrefId", [""])[0]
            for key, expected in [
                ("courseId", target.course_id),
                ("classId", target.class_id),
                ("cpi", target.cpi),
            ]:
                if key in params and params[key] != [expected]:
                    raise CompatibilityError("考试行课程标识与配置不一致")
            name = row.select_one("p")
            status = row.select_one("span:not(.fr)")
        else:
            node = row.select_one("[onclick]")
            handler = node.get("onclick", "") if node else ""
            # Parse IDs only; never execute JavaScript scraped from HTML.
            m = re.fullmatch(
                r"\s*(?:viewExamAnswer|startExam|toExam|enterExam)\(\s*['\"](\d+)['\"](?:\s*,[^)]*)?\)\s*;?\s*",
                handler,
            )
            if not m:
                raise CompatibilityError("未知电脑端考试入口，不能可靠读取考试 ID")
            eid, entry_url = m[1], ""
            name, status = row.select_one("p.overHidden2"), row.select_one("p.status")
        if not eid.isdigit() or name is None or status is None:
            raise CompatibilityError("考试 ID/名称/状态缺失")
        text = row.get_text(" ", strip=True)
        exams.append(
            Exam(
                exam_id=eid,
                name=name.get_text(strip=True),
                target=target,
                state=STATES.get(status.get_text(strip=True), ExamState.UNKNOWN),
                starts_at=_date(text, "开始时间"),
                deadline=_date(text, "截止时间"),
                entry_url=entry_url,
            )
        )
    if len({e.exam_id for e in exams}) != len(exams):
        raise CompatibilityError("考试列表 ID 重复")
    return exams


def parse_questions(html, *, first_number=1):
    soup = guard_html(html)
    nodes = soup.select("div.questionWrap.singleQuesId.ans-cc-exam")
    if not nodes:
        raise CompatibilityError("没有已知选择题结构，禁止静默遗漏")
    questions = []
    for index, node in enumerate(nodes, first_number):
        qid = node.select_one("input[name=questionId]")
        typ = node.select_one("input[name^=type]")
        title = node.select_one("div.tit")
        if qid is None or typ is None or title is None:
            raise CompatibilityError("题目 ID/题型/题干结构缺失")
        if typ.get("value") not in {"0", "1"}:
            raise CompatibilityError("存在非单选/多选题，不能遗漏后自动交卷")
        # Clone title and remove only known metadata nodes; never use positional children slicing.
        clean = BeautifulSoup(str(title), "html.parser")
        for metadata in clean.select("h3, span.grayTit"):
            metadata.decompose()
        text = clean.get_text("\n", strip=True)
        number_match = re.match(r"^(\d+)[.、．]\s*", text)
        number = int(number_match[1]) if number_match else index
        text = re.sub(r"^\d+[.、．]\s*", "", text)
        options = {}
        for option in node.select("div.answerList.radioList"):
            key = option.get("name", "")
            content = option.select_one("cc")
            if key in options or content is None:
                raise CompatibilityError("选项重复或结构异常")
            options[key] = content.get_text("\n", strip=True)
        answer_node = node.select_one("input[id^=answer]")
        answer = answer_node.get("value", "") if answer_node else ""
        media = [x.get("src", "") for x in node.select("img")]
        if node.select_one("math, svg, canvas, .MathJax, .katex"):
            media.append("unparsed-math-or-vector")
        # Empty image options are still detected as media before validating text.
        for key in options:
            if not options[key] and media:
                options[key] = "[图片内容未读取]"
        try:
            q = Question(
                question_id=qid.get("value", ""),
                number=number,
                type="single_choice" if typ["value"] == "0" else "multiple_choice",
                question=text or ("[图片题干未读取]" if media else ""),
                options=options,
                existing_answer=list(answer),
                media=media,
            )
        except ValueError as exc:
            raise CompatibilityError("题目字段校验失败") from exc
        if not q.question_id.isdigit():
            raise CompatibilityError("异常题目 ID")
        questions.append(q)
    if len({q.question_id for q in questions}) != len(questions):
        raise CompatibilityError("题目 ID 重复")
    if len({q.number for q in questions}) != len(questions):
        raise CompatibilityError("题号重复")
    return questions
