from dasik.lib.command_worker.su_argv import su_argv


def test_su_argv_shape():
    assert su_argv("u", 's "$1"', "a b") == [
        "-", "u", "-c", 's "$1"', "--", "sh", "a b"]
