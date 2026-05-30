from unittest.mock import patch, MagicMock

from dasik import __main__ as cli
from dasik.lib.state.generation_store import GenInfo


def test_generations_lists_with_current_marker(capsys):
    with patch("dasik.__main__.GenerationStore") as Gen:
        Gen.return_value.list.return_value = [
            GenInfo(number=1, is_current=False),
            GenInfo(number=2, is_current=True),
        ]
        rc = cli.main(["generations"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "1" in out
    assert "2" in out
    assert "current" in out.lower()
    # The current marker is on generation 2, not 1.
    line2 = [ln for ln in out.splitlines() if "2" in ln][0]
    assert "current" in line2.lower()


def test_generations_empty_prints_message(capsys):
    with patch("dasik.__main__.GenerationStore") as Gen:
        Gen.return_value.list.return_value = []
        rc = cli.main(["generations"])

    assert rc == 0
    assert "no generations" in capsys.readouterr().out.lower()


def test_generations_default_target_is_root():
    with patch("dasik.__main__.GenerationStore") as Gen:
        Gen.return_value.list.return_value = []
        cli.main(["generations"])
    assert Gen.call_args.args[0].root == "/"


def test_generations_explicit_target():
    with patch("dasik.__main__.GenerationStore") as Gen:
        Gen.return_value.list.return_value = []
        cli.main(["generations", "--target", "/mnt"])
    assert Gen.call_args.args[0].root == "/mnt"
