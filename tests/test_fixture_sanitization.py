import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKED_SUFFIXES = {".csv", ".json", ".md", ".py", ".txt", ".yaml", ".yml"}
EXCLUDED_PATHS = {
    Path("tests/FIXTURE_POLICY.md"),
    Path("tests/test_fixture_sanitization.py"),
}
PROHIBITED_PATTERNS = {
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "email_address": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


def iter_fixture_candidates():
    roots = [ROOT / "tests"]
    for optional_root in ("fixtures", "testdata"):
        path = ROOT / optional_root
        if path.exists():
            roots.append(path)

    for root in roots:
        for path in root.rglob("*"):
            relative = path.relative_to(ROOT)
            if path.is_file() and path.suffix in CHECKED_SUFFIXES:
                if relative not in EXCLUDED_PATHS:
                    yield relative, path


def test_test_fixtures_do_not_contain_raw_realistic_samples():
    findings = []
    for relative, path in iter_fixture_candidates():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, pattern in PROHIBITED_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative}: {name}")

    assert findings == []
