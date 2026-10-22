from codereviewer.diff import parse_unified_diff


def test_quoted_paths_and_header_like_added_lines():
    diff = parse_unified_diff(
        'diff --git "a/my file.ts" "b/my file.ts"\n'
        '--- "a/my file.ts"\n+++ "b/my file.ts"\n'
        "@@ -1 +1 @@\n-old\n+++ value\n"
    )
    assert diff.files[0].path == "my file.ts"
    assert diff.files[0].changed_lines[-1].content == "++ value"


DIFF = """diff --git a/app/service.py b/app/service.py
index 1111111..2222222 100644
--- a/app/service.py
+++ b/app/service.py
@@ -10,3 +10,4 @@ def run():
 context()
-old_call()
+new_call()
+audit()
 return True
"""


def test_parse_unified_diff_maps_old_and_new_lines() -> None:
    parsed = parse_unified_diff(DIFF)

    assert len(parsed.files) == 1
    assert parsed.files[0].path == "app/service.py"
    assert parsed.files[0].new_changed_lines == {11, 12}
    assert parsed.inline_location("app/service.py", 11) == (11, "RIGHT")
    assert parsed.inline_location("app/service.py", 99) is None
    deleted = next(line for line in parsed.files[0].changed_lines if line.side == "LEFT")
    assert deleted.old_line == 11


def test_parse_unified_diff_handles_new_file() -> None:
    parsed = parse_unified_diff(
        """diff --git a/dev/null b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+def hello():
+    return 'world'
"""
    )

    assert parsed.files[0].path == "new.py"
    assert parsed.files[0].new_changed_lines == {1, 2}
