import asyncio
import logging
import os
import sys
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("autoexam")


class Notifier:
    def __init__(self, cfg):
        self.cfg = cfg
        self.events = []
        self.failures = 0

    async def emit(self, event, text):
        # Caller supplies sanitized summaries only, never HTTP bodies/questions/secrets.
        self.events.append(event)
        log.info("event=%s %s", event, text)
        if not self.cfg.enabled:
            return
        if self.cfg.macos and sys.platform == "darwin":
            try:
                # Arguments are argv, never interpolated AppleScript or shell code.
                script = "on run argv\ndisplay notification (item 1 of argv) with title (item 2 of argv)\nend run"
                proc = await asyncio.create_subprocess_exec(
                    "/usr/bin/osascript",
                    "-e",
                    script,
                    text,
                    "ExamPilot",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), 3)
                if proc.returncode:
                    raise RuntimeError
            except (OSError, TimeoutError, RuntimeError):
                self.failures += 1
                log.warning("macOS 通知发送失败")
        if self.cfg.mobile:
            url = os.environ.get("AUTOEXAM_NTFY_URL", "")
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
                self.failures += 1
                log.warning("手机通知缺少有效 HTTPS ntfy 配置")
                return
            headers = {"Title": "ExamPilot"}
            token = os.environ.get("AUTOEXAM_NTFY_TOKEN")
            if token:
                headers["Authorization"] = "Bearer " + token
            try:
                async with httpx.AsyncClient(
                    timeout=3, follow_redirects=False, trust_env=False
                ) as c:
                    r = await c.post(url, content=text.encode("utf-8"), headers=headers)
                    if not 200 <= r.status_code < 300:
                        raise RuntimeError
            except (httpx.HTTPError, RuntimeError):
                self.failures += 1
                log.warning("手机通知发送失败；未记录地址/Token")
