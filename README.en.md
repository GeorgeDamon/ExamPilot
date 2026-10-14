# ExamPilot

**Open-source Chaoxing / Xuexitong course-exam monitoring and DeepSeek answer automation for macOS.**

[中文说明](README.md) · [Usage](docs/USAGE.md) · [Contributing](CONTRIBUTING.md) · [Validation](docs/TEST_REPORT.md)

ExamPilot brings course-scoped monitoring, batched choice-question answering, persistent task state, conservative submission reconciliation, macOS scheduling and notifications into one Python project.

**Status: `0.1.0a1` Alpha, `dry_run=true` by default.** The synthetic end-to-end flow, one live login/session restoration, course-list reading and synthetic-question DeepSeek calls have been checked. Reading, saving and submitting a current live PC exam remain unverified. This release does not guarantee unattended completion or correct answers.

## Start with the local simulator

```sh
git clone https://github.com/GeorgeDamon/ExamPilot.git
cd ExamPilot
./install.sh
.venv/bin/exampilot demo
.venv/bin/exampilot demo --lost-response
```

The simulator requires no account or API key and does not contact Chaoxing or DeepSeek. It uses synthetic questions and a loopback HTTP server, including a test where the server commits a submission then closes the connection without returning a response.

For CLI-only installation:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/exampilot init
.venv/bin/exampilot demo
```

Python 3.11+ is required. macOS Apple Silicon is the deployment target. The full source checkout includes launchd scripts; the wheel contains the CLI and blank templates. `EXAMPILOT_HOME` selects a writable working directory for an installed wheel.

## Features and boundaries

- Monitor one configured course; deduplicate using exam IDs.
- Use the official DeepSeek API with bounded concurrency, JSON validation and selective repair.
- Store state and timings in SQLite without a question bank.
- Reconcile uncertain submissions rather than blindly resubmitting.
- Provide single-instance execution, failure pause, manual controls, macOS notifications and optional ntfy.
- Stop on unknown page structures, unread media or identity verification requirements.

Use the project only in automation scenarios permitted by your school/course and for supervised practice or local research. It includes no CAPTCHA cracking, fabricated face verification, security bypass, video farming or third-party answer-search providers.

Keep keys, cookies and signed course URLs in local private files. Review [Usage](docs/USAGE.md) before connecting a real course. Structural answer validation is not proof of correctness. Server confirmation is required before a task becomes `SUBMITTED`.

Contributions using synthetic or sanitized fixtures are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and [NOTICE.md](NOTICE.md). Licensed under **GPL-3.0-only**. ExamPilot is not affiliated with Chaoxing.

