import fcntl
import hashlib
import json
import logging
import os
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .store import private_dir, private_json
from .models import utcnow


class InstanceLock:
    def __init__(self, runtime: Path):
        self.runtime = private_dir(runtime)
        self.file = None
        self.stopped = False

    def __enter__(self):
        self.file = (self.runtime / "instance.lock").open("a+")
        os.chmod(self.file.name, 0o600)
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeError("监控已有实例在运行") from None
        (self.runtime / "stop.request").unlink(missing_ok=True)
        self.previous_handlers = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda *_: setattr(self, "stopped", True))
        self.heartbeat()
        return self

    def heartbeat(self):
        private_json(
            self.runtime / "process.json", {"pid": os.getpid(), "heartbeat": utcnow().isoformat()}
        )

    def stop_requested(self):
        return self.stopped or (self.runtime / "stop.request").exists()

    def __exit__(self, *args):
        (self.runtime / "process.json").unlink(missing_ok=True)
        for sig, handler in self.previous_handlers.items():
            signal.signal(sig, handler)
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


def running(runtime):
    path = runtime / "instance.lock"
    if not path.exists():
        return False
    with path.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True


def setup_logging(runtime, level):
    private_dir(runtime)
    path = runtime / "autoexam.log"
    logger = logging.getLogger("autoexam")
    logger.handlers.clear()
    logger.setLevel(level)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    path.chmod(0o600)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    # Never enable HTTP or browser debug output carrying query tokens.
    for name in ("httpx", "httpcore", "playwright"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def load_env(path):
    if not path.exists():
        return
    if path.stat().st_mode & 0o077:
        raise ValueError(".env 权限必须为 600")
    allowed = {
        "DEEPSEEK_API_KEY",
        "AUTOEXAM_NTFY_URL",
        "AUTOEXAM_NTFY_TOKEN",
        "AUTOEXAM_COURSE_URL",
    }
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in allowed:
            raise ValueError(".env 包含未知字段；不执行 shell 指令")
        if value:
            os.environ.setdefault(key, value.strip().strip("\"'"))


def code_hash(root):
    h = hashlib.sha256()
    source = root / "autoexam"
    if not source.is_dir():
        source = Path(__file__).resolve().parent
    for path in sorted(source.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def validate_receipt(root, runtime, target):
    try:
        receipt = json.loads((runtime / "validation.json").read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return (
        receipt.get("code_sha256") == code_hash(root)
        and receipt.get("target") == target.model_dump()
        and receipt.get("mock_chain_passed") is True
        and receipt.get("live_read_passed") is True
    )
