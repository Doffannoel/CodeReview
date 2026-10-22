from codereviewer.diff import parse_unified_diff
from codereviewer.providers.base import ConflictDetail, FileReview, Finding, ReviewResult
from codereviewer.review import (
    build_prompt,
    finding_markdown,
    normalize_file_reviews,
    summary_markdown,
    validate_findings,
)


def test_invalid_line_becomes_summary_finding_and_duplicates_are_removed() -> None:
    diff = parse_unified_diff(
        """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,1 @@
-old()
+new()
"""
    )
    finding = Finding(
        path="app.py",
        start_line=99,
        severity="high",
        category="correctness",
        title="Wrong line",
        explanation="This cannot be placed inline.",
        suggestion="replacement",
        confidence=0.9,
    )
    result = ReviewResult(summary="Done", findings=[finding, finding])

    validated = validate_findings(result, diff, max_findings=10)

    assert len(validated) == 1
    assert not validated[0].is_inline
    assert validated[0].finding.start_line is None
    assert validated[0].finding.suggestion is None


def test_valid_line_remains_inline() -> None:
    diff = parse_unified_diff(
        """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,1 @@
-old()
+new()
"""
    )
    result = ReviewResult(
        summary="Done",
        file_reviews=[FileReview(path="app.py", change_summary="Replaces the old call.")],
        impact="Changes the execution path used by callers.",
        provider="fake",
        model="fake-model",
        findings=[
            Finding(
                path="app.py",
                start_line=1,
                severity="medium",
                category="reliability",
                title="Potential failure",
                explanation="The call can fail.",
                confidence=0.9,
                conflict=ConflictDetail(
                    target="worker.py::run",
                    description="The caller expects this operation not to raise.",
                ),
            )
        ],
    )
    validated = validate_findings(result, diff, 10)

    assert validated[0].is_inline
    summary = summary_markdown(
        result,
        validated,
        "111111111111111",
        "abcdef123456789",
        diff,
    )
    assert "# Code Review Results" in summary
    assert "## Summary" in summary
    assert "## PR Detail" in summary
    assert "`111111111111` → `abcdef123456`" in summary
    assert "| `app.py` | Replaces the old call. |" in summary
    assert "## Impact" in summary
    assert "Changes the execution path used by callers." in summary
    assert "## Risk" in summary
    assert "| 🟡 Medium | 1 |" in summary
    assert "### Findings" in summary
    assert "| `app.py` | Medium | Potential failure | worker.py::run |" in summary
    assert "Conflict: The caller expects this operation not to raise." in summary
    assert "`abcdef123456`" in summary


def test_file_review_table_uses_only_diff_paths_and_fills_missing_files() -> None:
    diff = parse_unified_diff(
        """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old()
+new()
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -1 +1,2 @@
 VALUE = 1
+ENABLED = True
"""
    )
    result = ReviewResult(
        summary="Done",
        file_reviews=[
            FileReview(path="b/app.py", change_summary="Replaces old | call."),
            FileReview(path="invented.py", change_summary="Not part of the diff."),
        ],
    )

    normalized = normalize_file_reviews(result, diff)
    markdown = summary_markdown(result, [], "base", "head", diff)

    assert [item.path for item in normalized] == ["app.py", "config.py"]
    assert normalized[0].change_summary == "Replaces old | call."
    assert normalized[1].change_summary == "Changed in this pull request (+1/-0 lines)."
    assert "invented.py" not in markdown
    assert "Replaces old \\| call." in markdown


def test_prompt_requests_summary_file_details_impact_and_findings() -> None:
    diff = parse_unified_diff(
        """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old()
+new()
"""
    )
    prompt = build_prompt(
        {
            "title": "Update app",
            "body": "Change behavior",
            "base": {"sha": "base"},
            "head": {"sha": "head"},
        },
        diff,
        [],
        10_000,
    )

    assert "exactly one file_reviews entry for every changed file" in prompt
    assert "impact analysis comparing the change" in prompt
    assert "finding.conflict.target" in prompt


def test_exact_replacement_is_rendered_as_github_suggestion() -> None:
    finding = Finding(
        path="app.py",
        start_line=1,
        severity="medium",
        category="correctness",
        title="Replace the unsafe call",
        explanation="The safe call handles missing values.",
        conflict=ConflictDetail(
            target="payment.py::capture contract",
            description="The current caller permits a missing payment identifier.",
        ),
        suggestion="```python\nsafe_call()\n```",
    )

    markdown = finding_markdown(finding, github_suggestion=True)

    assert "```suggestion\nsafe_call()\n```" in markdown
    assert "```python" not in markdown
    assert "**Conflicts with:** payment.py::capture contract" in markdown
    assert "The current caller permits a missing payment identifier." in markdown
