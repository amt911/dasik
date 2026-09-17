"""No test may inherit git's GIT_* environment.

git exports GIT_DIR (and friends) to hooks — from a worktree, GIT_DIR points at
<repo>/.git/worktrees/<name>. The pre-push hook runs this suite, and
tests/lib/test_git_save.py / test_main_save.py drive `git` in temporary
repositories: with GIT_DIR inherited, every one of those commands acted on the
REAL repository instead. On 2026-09-17 that committed 11 test commits onto a
pushed branch and wrote `core.bare=true` and `user.name=Test` into dasik's own
.git/config, so ~20 published commits were authored "Test <t@example.com>".

The hook now unsets them too, but a developer running pytest from any git hook,
alias or editor integration that exports GIT_DIR would hit the same thing.
"""
import os


def test_no_git_environment_reaches_a_test():
    leaked = sorted(k for k in os.environ if k.startswith("GIT_"))
    assert leaked == []
