"""Conservative public-tree check. Never prints matching credential content."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IGNORED = {
    ".git",
    ".venv",
    ".browser",
    "runtime",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
SECRET = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def candidates():
    if (ROOT / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
        )
        tracked = [ROOT / p.decode() for p in result.stdout.split(b"\0") if p]
        if tracked:
            return tracked
    return [
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and not (set(p.relative_to(ROOT).parts) & IGNORED)
        and not p.name.endswith((".zip", ".bundle", ".whl", ".pyc"))
    ]


def main():
    issues = []
    for path in candidates():
        rel = path.relative_to(ROOT)
        if path.is_symlink():
            issues.append(f"{rel}: symlink not permitted in release source")
            continue
        if (
            path.name in {".env", "config.yaml", "session.json"}
            or path.suffix in {".plist", ".sqlite3", ".log"}
            or "runtime" in rel.parts
        ):
            issues.append(f"{rel}: private/generated file included")
        data = path.read_bytes()
        if SECRET.search(data):
            issues.append(f"{rel}: credential pattern detected (value hidden)")
        if ("/Users/" + "mac/").encode() in data:
            issues.append(f"{rel}: original local user path detected")
    if issues:
        print("\n".join(issues))
        return 1
    print("Public source content check PASS; matching values are never printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
