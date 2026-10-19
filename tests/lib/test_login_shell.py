"""Reading a command's output through `su - <user>`, a LOGIN shell.

A login profile may print (a greeting, fastfetch, a motd) before the command
runs, and that text lands in the same stdout. Found in a guest: `dasik save`
read the repository root from `git rev-parse` as
"/home/test/cfg/hello from the profile\\n/home/test/cfg" and refused to commit.
"""
from types import SimpleNamespace

from dasik.lib import git_save
from dasik.lib.login_shell import MARKED, strip_login_noise


def test_the_marked_script_prints_the_marker_before_the_command():
    assert MARKED('git "$@"') == "printf '\\036dasik\\036'; " + 'git "$@"'


def test_noise_before_the_marker_is_dropped_bytes_and_str():
    for out in (b"hello\n\x1edasik\x1e/home/x\n", "hello\n\x1edasik\x1e/home/x\n"):
        done = strip_login_noise(SimpleNamespace(stdout=out))
        assert done.stdout in (b"/home/x\n", "/home/x\n")


def test_output_without_the_marker_is_left_alone():
    done = strip_login_noise(SimpleNamespace(stdout=b"/home/x\n"))
    assert done.stdout == b"/home/x\n"


def test_git_save_reads_the_repo_root_past_a_chatty_profile(tmp_path, monkeypatch):
    calls = []

    def execute(cmd, args, **kwargs):
        calls.append([cmd, *args])
        return SimpleNamespace(returncode=0,
                               stdout=b"hello from the profile\n\x1edasik\x1e/home/test/cfg\n",
                               stderr=b"")

    monkeypatch.setattr(git_save.Command, "execute", staticmethod(execute))

    assert git_save.repo_root(tmp_path, user="test") == tmp_path.__class__("/home/test/cfg")
    assert calls[0][:5] == ["su", "-", "test", "-c", MARKED('git "$@"')]
