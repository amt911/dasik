"""The curses layer, driven by a script of keys against a fake screen.

The issue asks for exactly this: "la interacción en sí (curses/prompt) es la
capa fina de encima y se prueba con un guion de teclas, no con un disco". So the
screens only ever touch a handful of curses methods, and a fake implementing
those is enough to drive the whole flow — including the paths a human would
have to be quick to hit, like backing out of the last screen.
"""
import curses

import pytest

from dasik.lib.wizard.inventory import DiskInfo, PartitionInfo
from dasik.lib.wizard.tui import Choices, confirm, menu, prompt, run_wizard

ENTER = 10
ESC = 27
BACKSPACE = curses.KEY_BACKSPACE
DOWN = curses.KEY_DOWN
UP = curses.KEY_UP


class FakeScreen:
    """The five curses calls the wizard makes, and a queue of keystrokes."""

    def __init__(self, keys, height=24, width=80):
        self._keys = list(keys)
        self._size = (height, width)
        self.lines = []          # everything ever drawn, for assertions

    # -- the curses surface ------------------------------------------- #
    def getmaxyx(self):
        return self._size

    def erase(self):
        pass

    def refresh(self):
        pass

    def addstr(self, y, x, text, *attrs):
        self.lines.append(text)

    def getch(self):
        if not self._keys:
            raise AssertionError(
                "the wizard asked for a key and the script had none left; "
                f"drawn so far: {self.lines[-6:]}")
        return self._keys.pop(0)

    # -- helpers for the tests ----------------------------------------- #
    def drawn(self):
        return "\n".join(self.lines)


def _keys(text):
    """A string as the keystrokes that type it."""
    return [ord(c) for c in text]


_EMPTY_DISK = DiskInfo(path="/dev/vda", size=8589934592, pttype="")
_FULL_DISK = DiskInfo(
    path="/dev/sda", size=4000787030016, pttype="gpt",
    partitions=(PartitionInfo("/dev/sda1", 104857600, "ntfs", "Windows",
                              "/run/media/x"),))


# --- the primitives --------------------------------------------------------- #

def test_menu_returns_the_index_of_the_chosen_row():
    screen = FakeScreen([DOWN, DOWN, ENTER])

    assert menu(screen, "Pick", ["a", "b", "c"]) == 2


def test_menu_starts_on_the_first_row():
    assert menu(FakeScreen([ENTER]), "Pick", ["a", "b"]) == 0


def test_menu_does_not_run_off_either_end():
    assert menu(FakeScreen([UP, UP, ENTER]), "Pick", ["a", "b"]) == 0
    assert menu(FakeScreen([DOWN, DOWN, DOWN, ENTER]), "Pick", ["a", "b"]) == 1


def test_menu_can_be_abandoned():
    assert menu(FakeScreen([ord("q")]), "Pick", ["a"]) is None
    assert menu(FakeScreen([ESC]), "Pick", ["a"]) is None


def test_menu_draws_the_title_and_every_row():
    screen = FakeScreen([ENTER])

    menu(screen, "Disks", ["/dev/vda", "/dev/sda"])

    assert "Disks" in screen.drawn()
    assert "/dev/vda" in screen.drawn() and "/dev/sda" in screen.drawn()


def test_prompt_collects_typed_text():
    screen = FakeScreen(_keys("cryptroot") + [ENTER])

    assert prompt(screen, "LUKS", "mapper name", default="x") == "cryptroot"


def test_prompt_returns_the_default_when_nothing_is_typed():
    assert prompt(FakeScreen([ENTER]), "LUKS", "name", default="cryptroot") \
        == "cryptroot"


def test_prompt_handles_backspace():
    screen = FakeScreen(_keys("abc") + [BACKSPACE] + _keys("d") + [ENTER])

    assert prompt(screen, "t", "l", default="") == "abd"


def test_prompt_can_be_abandoned():
    assert prompt(FakeScreen([ESC]), "t", "l", default="d") is None


def test_prompt_hides_a_secret():
    screen = FakeScreen(_keys("hunter2") + [ENTER])

    assert prompt(screen, "LUKS", "passphrase", default="", secret=True) == "hunter2"
    assert "hunter2" not in screen.drawn()
    assert "*******" in screen.drawn()


def test_confirm_is_no_by_default():
    assert confirm(FakeScreen([ENTER]), "Erase?", "sure?") is False
    assert confirm(FakeScreen([ord("y")]), "Erase?", "sure?") is True
    assert confirm(FakeScreen([ord("n")]), "Erase?", "sure?") is False


# --- the whole flow --------------------------------------------------------- #

def _run(keys, disks=(_EMPTY_DISK,)):
    return run_wizard(FakeScreen(keys), list(disks))


def test_the_shortest_path_picks_a_disk_a_recipe_and_accepts_the_defaults():
    # disk (Enter) · recipe ext4 (Enter) · esp size (Enter) · hostname (Enter)
    # · review (Enter = write)
    choices = _run([ENTER, ENTER, ENTER, ENTER, ENTER])

    assert isinstance(choices, Choices)
    assert choices.device == "/dev/vda"
    assert choices.recipe_key == "ext4"
    assert choices.options.esp_size == "512MiB"
    assert choices.passphrase is None          # ext4 asks for none


def test_an_encrypted_recipe_asks_for_the_passphrase_and_keeps_it_out_of_the_screen():
    # disk · recipe #2 (luks-btrfs) · esp size · luks name · passphrase · hostname · review
    screen = FakeScreen([ENTER, DOWN, ENTER, ENTER, ENTER]
                        + _keys("hunter2") + [ENTER, ENTER, ENTER])

    choices = run_wizard(screen, [_EMPTY_DISK])

    assert choices.recipe_key == "luks-btrfs"
    assert choices.passphrase == "hunter2"
    assert "hunter2" not in screen.drawn()


def test_a_disk_that_holds_data_has_to_be_confirmed_before_it_is_wiped():
    # disk · recipe ext4 · WIPE? (y) · esp size · hostname · review
    choices = _run([ENTER, ENTER, ord("y"), ENTER, ENTER, ENTER],
                   disks=(_FULL_DISK,))

    assert choices.options.wipe is True


def test_declining_the_wipe_abandons_rather_than_composing_something_useless():
    """A populated disk without `wipe_disk` is refused by plan() anyway — dasik
    never silently reformats. Composing it would produce a config that cannot
    install and does not say why."""
    assert _run([ENTER, ENTER, ord("n")], disks=(_FULL_DISK,)) is None


def test_an_empty_disk_is_never_asked_about_wiping():
    # No wipe question: disk · recipe · esp size · hostname · review
    choices = _run([ENTER, ENTER, ENTER, ENTER, ENTER])

    assert choices.options.wipe is False


def test_quitting_at_the_first_screen_returns_nothing():
    assert _run([ord("q")]) is None


def test_quitting_at_the_review_returns_nothing():
    assert _run([ENTER, ENTER, ENTER, ENTER, ord("q")]) is None


def test_the_review_shows_the_layout_and_the_warnings_before_anything_is_written():
    screen = FakeScreen([ENTER, ENTER, ord("y"), ENTER, ENTER, ENTER])

    run_wizard(screen, [_FULL_DISK])

    drawn = screen.drawn()
    assert "/dev/sda" in drawn
    assert "ERASE" in drawn.upper()
    assert "ESP" in drawn and "root" in drawn


def test_no_disks_at_all_is_said_out_loud():
    screen = FakeScreen([ENTER])

    assert run_wizard(screen, []) is None
    assert "no disks" in screen.drawn().lower()


def test_the_custom_row_is_offered_and_composes_from_typed_partitions():
    """Recipes are the normal path; this is the way out for everything else."""
    # disk · recipe = last row (custom) · then one partition: label, size, fs,
    # mountpoint · "add another?" no · hostname · review
    keys = ([ENTER] + [DOWN] * 4 + [ENTER]
            + _keys("ESP") + [ENTER] + _keys("512MiB") + [ENTER] + [ENTER]
            + _keys("/boot") + [ENTER] + [ord("y")]
            + _keys("root") + [ENTER] + _keys("rest") + [ENTER] + [DOWN, ENTER]
            + _keys("/") + [ENTER] + [ord("n")]
            + [ENTER, ENTER])

    choices = _run(keys)

    assert choices.recipe_key == "custom"
    assert [p["label"] for p in choices.custom_partitions] == ["ESP", "root"]
    assert choices.custom_partitions[0]["filesystem"] == "fat32"
    assert choices.custom_partitions[1]["size"] == "rest"
