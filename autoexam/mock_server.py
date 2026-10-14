"""Local simulated HTTP server; synthetic questions, not a historical question bank."""

import json
import socket
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .models import Exam, ExamState, Question, Target, utcnow


class World:
    def __init__(self, target=None, *, count=2, page_size=1):
        self.target = target or Target(course_id="1", class_id="2", cpi="3")
        self.exams = [
            Exam(
                exam_id="9001",
                name="模拟选择题考试",
                target=self.target,
                state=ExamState.AVAILABLE,
                deadline=utcnow() + timedelta(minutes=5),
                starts_at=utcnow() - timedelta(minutes=1),
                duration_seconds=300,
            )
        ]
        self.questions = [
            Question(
                question_id=str(1001 + i),
                number=i + 1,
                type="single_choice" if i % 2 == 0 else "multiple_choice",
                question="模拟题：选择数字 2" if i % 2 == 0 else "模拟题：选择偶数",
                options={"A": "2", "B": "3", "C": "4", "D": "5"},
            )
            for i in range(count)
        ]
        self.saved = {}
        self.submit_calls = 0
        self.save_calls = 0
        self.enter_calls = 0
        self.ai_calls = 0
        self.page_size = page_size
        self.session_expired = False
        self.lost_submit_response = False
        self.submit_fails = False
        self.state_unknown = False
        self.network_failures = 0
        self.bad_ai = []
        self.lock = threading.RLock()

    def response(self, method, path, payload):
        p = urlsplit(path)
        query = parse_qs(p.query)
        with self.lock:
            if p.path == "/chat/completions":
                self.ai_calls += 1
                if self.bad_ai:
                    content = self.bad_ai.pop(0)
                else:
                    messages = payload["messages"]
                    try:
                        questions = json.loads(messages[1]["content"])["questions"]
                    except (ValueError, KeyError):
                        questions = []
                    content = json.dumps(
                        {
                            "answers": [
                                {
                                    "question_id": q["question_id"],
                                    "answer": ["A"] if q["type"] == "single_choice" else ["A", "C"],
                                    "uncertain": False,
                                }
                                for q in questions
                            ]
                        }
                    )
                return 200, {
                    "choices": [{"message": {"content": content}, "finish_reason": "stop"}]
                }
            if self.session_expired:
                return 401, {"error": "session expired"}
            if self.network_failures:
                self.network_failures -= 1
                return 503, {"error": "temporary outage"}
            if p.path == "/health":
                return 200, {"target": self.target.model_dump()}
            if p.path == "/exams":
                return 200, {"exams": [e.model_dump(mode="json") for e in self.exams]}
            eid = query.get("exam_id", [payload.get("exam_id")])[0]
            exam = next((e for e in self.exams if e.exam_id == eid), None)
            if not exam:
                return 404, {"error": "not found"}
            if p.path == "/state":
                return 200, {"state": "UNKNOWN" if self.state_unknown else exam.state.value}
            if p.path == "/enter":
                self.enter_calls += 1
                if exam.state not in {ExamState.AVAILABLE, ExamState.IN_PROGRESS}:
                    return 409, {"error": "not available"}
                exam.state = ExamState.IN_PROGRESS
                return 200, {"exam": exam.model_dump(mode="json")}
            if p.path == "/questions":
                page = int(query.get("page", [0])[0])
                start = page * self.page_size
                qs = self.questions[start : start + self.page_size]
                return 200, {
                    "total": len(self.questions),
                    "questions": [q.model_dump() for q in qs],
                    "next_page": page + 1 if start + self.page_size < len(self.questions) else None,
                }
            if p.path == "/save":
                self.save_calls += 1
                self.saved[eid] = {a["question_id"]: a["answer"] for a in payload["answers"]}
                return 200, {"saved": True}
            if p.path == "/saved":
                return 200, {"answers": self.saved.get(eid, {})}
            if p.path == "/submit":
                self.submit_calls += 1
                if self.submit_fails:
                    return 503, {"error": "submit failure"}
                if len(self.saved.get(eid, {})) != len(self.questions):
                    return 409, {"error": "missing answers"}
                exam.state = ExamState.SUBMITTED
                if self.lost_submit_response:
                    return 599, {"error": "response lost after commit"}
                return 200, {"ok": True}
            return 404, {"error": "unknown endpoint"}


class MockServer:
    def __init__(self, world=None):
        self.world = world or World()
        world_ref = self.world

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    if length > 2_000_000:
                        self.send_error(413)
                        return
                    data = json.loads(self.rfile.read(length)) if length else {}
                    code, body = world_ref.response(self.command, self.path, data)
                    if code == 599:
                        # Commit happened; close without an HTTP response to exercise real loss.
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = handle_request
            do_POST = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
