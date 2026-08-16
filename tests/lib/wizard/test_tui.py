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
from dasik.lib.wizard.tui import (Choices, confirm, label_error, menu, prompt,
                                  run_wizard, size_error)

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
        self._nodelay = False
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

    def nodelay(self, flag):
        """Real curses returns -1 instead of blocking; so does this."""
        self._nodelay = bool(flag)

    def getch(self):
        if not self._keys and self._nodelay:
            return -1
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
    """With `q`. NOT with ESC — see the split-arrow tests below."""
    assert menu(FakeScreen([ord("q")]), "Pick", ["a"]) is None


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
    # disk · recipe ext4 · [ERASE / simulate] -> row 1 = ERASE · esp · hostname
    # · review. The erase is never the default of a y/n; it is a row you pick.
    choices = _run([ENTER, ENTER, ENTER, ENTER, ENTER, ENTER],
                   disks=(_FULL_DISK,))

    assert choices.options.wipe is True


def test_not_erasing_composes_a_simulation_instead_of_abandoning():
    """It used to abandon, and that was wrong: an assistant that never applies
    is exactly the tool you want to point at a full disk to see what a layout
    WOULD be. The config is written with wipe_disk false, and the review says
    plan will skip the disk."""
    choices = _run([ENTER, ENTER, DOWN, ENTER, ENTER, ENTER, ENTER],
                   disks=(_FULL_DISK,))

    assert choices is not None and choices.options.wipe is False


def test_an_empty_disk_is_never_asked_about_wiping():
    # No wipe question: disk · recipe · esp size · hostname · review
    choices = _run([ENTER, ENTER, ENTER, ENTER, ENTER])

    assert choices.options.wipe is False


def test_quitting_at_the_first_screen_returns_nothing():
    assert _run([ord("q")]) is None


def test_quitting_at_the_review_returns_nothing():
    assert _run([ENTER, ENTER, ENTER, ENTER, ord("q")]) is None


def test_the_review_shows_the_layout_and_the_warnings_before_anything_is_written():
    screen = FakeScreen([ENTER, ENTER, ENTER, ENTER, ENTER, ENTER])

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


# --- typing something the model will refuse ---------------------------------- #

def test_a_bad_size_is_re_asked_instead_of_ending_the_session():
    """Seen on a VM: a stray key landed in the ESP size field, the recipe build
    raised deep inside curses, and the whole wizard died with a pydantic
    traceback dressed up as "could not start the wizard's screen". A prompt
    that knows what it is asking for can just ask again."""
    screen = FakeScreen(_keys("y") + [ENTER] + _keys("512MiB") + [ENTER])

    value = prompt(screen, "Sizes", "ESP size", default="512MiB",
                   validate=size_error)

    assert value == "512MiB"
    assert "unit" in screen.drawn().lower()      # it said why


def test_a_validated_prompt_still_takes_the_default():
    assert prompt(FakeScreen([ENTER]), "Sizes", "ESP size", default="512MiB",
                  validate=size_error) == "512MiB"


def test_a_validated_prompt_can_still_be_abandoned():
    assert prompt(FakeScreen([ESC]), "Sizes", "s", default="", validate=size_error) is None


@pytest.mark.parametrize("value", ["512MiB", "50%", "rest", "1GB", "8GiB"])
def test_the_sizes_the_model_accepts_pass_the_prompt_check(value):
    assert size_error(value) is None


@pytest.mark.parametrize("value", ["y", "512", "big", ""])
def test_the_sizes_it_does_not_are_named(value):
    assert size_error(value)


@pytest.mark.parametrize("value", ["-1MiB", "abcMiB", "0MiB"])
def test_the_prompt_is_exactly_as_strict_as_the_model_and_no_more(value):
    """These are nonsense and the model accepts them: `validate_size` only
    checks the SUFFIX. Filed as its own issue — the wizard deliberately mirrors
    the schema rather than inventing a second, stricter set of rules, which is
    the divergence that took 247 lines to remove from the action shims (#238).
    When the model tightens, this prompt tightens with it, and this test is the
    one that will fail to say so.
    """
    assert size_error(value) is None


def test_a_bad_label_is_re_asked_too():
    screen = FakeScreen(_keys("a/b") + [ENTER] + _keys("root") + [ENTER])

    assert prompt(screen, "Partition", "label", default="",
                  validate=label_error) == "root"


def test_the_whole_flow_survives_a_typo_in_a_size():
    # disk · recipe ext4 · esp size "y" (refused, re-asked) · "512MiB" · hostname · review
    keys = ([ENTER, ENTER] + _keys("y") + [ENTER] + _keys("512MiB")
            + [ENTER, ENTER, ENTER])

    choices = _run(keys)

    assert choices is not None
    assert choices.options.esp_size == "512MiB"


# --- a full disk must not force an erase ------------------------------------ #
#
# Reported after using it: on a disk with no free space the wizard asked "erase
# it?", and answering no ABANDONED the session. There was no way to say "just
# compose the block and show me" — which is the whole point of an assistant that
# never applies.

def test_a_populated_disk_offers_composing_without_erasing():
    # disk · layout · [erase / simulate] -> row 2 = simulate · esp · hostname · review
    choices = _run([ENTER, ENTER, DOWN, ENTER, ENTER, ENTER, ENTER],
                   disks=(_FULL_DISK,))

    assert choices is not None
    assert choices.options.wipe is False


def test_choosing_to_erase_still_sets_the_destructive_flag():
    choices = _run([ENTER, ENTER, ENTER, ENTER, ENTER, ENTER], disks=(_FULL_DISK,))

    assert choices.options.wipe is True


def test_the_simulation_says_what_plan_will_do_with_it():
    """A populated disk with wipe off is SKIPPED by plan — dasik never silently
    reformats. The review has to say that, or the config looks installable."""
    screen = FakeScreen([ENTER, ENTER, DOWN, ENTER, ENTER, ENTER, ENTER])

    run_wizard(screen, [_FULL_DISK])

    drawn = screen.drawn().lower()
    assert "skip" in drawn or "not be repartitioned" in drawn


def test_the_erase_screen_can_still_be_abandoned():
    assert _run([ENTER, ENTER, ord("q")], disks=(_FULL_DISK,)) is None


# --- the layout rows have to say what they give you -------------------------- #

def test_every_layout_row_names_its_whole_layout():
    """The rows read '…and a swap with a random key', which does not say it
    includes LUKS and btrfs — reported as "no sé cómo elegir btrfs + swap
    cifrada". Each row must stand on its own."""
    screen = FakeScreen([ENTER, ord("q")])

    run_wizard(screen, [_EMPTY_DISK])

    rows = [line for line in screen.lines if "ESP" in line or "Custom" in line]
    assert rows, "the layout menu was never drawn"
    assert not any(row.lstrip().startswith("…") for row in rows)
    for row in rows:
        if "Custom" not in row:
            assert "ESP" in row


def test_the_selected_layout_is_described_partition_by_partition():
    screen = FakeScreen([ENTER, DOWN, ord("q")])

    run_wizard(screen, [_EMPTY_DISK])

    drawn = screen.drawn()
    assert "@home" in drawn          # the subvolumes of the highlighted row
    assert "/boot" in drawn


def test_menu_shows_the_detail_of_the_row_under_the_cursor():
    screen = FakeScreen([DOWN, ENTER])

    menu(screen, "Pick", ["a", "b"], details=["detail A", "detail B"])

    assert "detail B" in screen.drawn()


# --- a stray ESC must not end the session ------------------------------------ #
#
# An arrow key IS an escape sequence (ESC [ B). On a slow serial line the ESC
# can arrive alone, ncurses' ESCDELAY expires, and getch() hands back a bare 27
# — which used to abandon the whole wizard. Proved with a pty that delivers
# [10, 27, 91] instead of KEY_DOWN: a menu that quits on ESC quits on an arrow.

def test_a_bare_escape_does_not_leave_a_menu():
    screen = FakeScreen([ESC, DOWN, ENTER])

    assert menu(screen, "Pick", ["a", "b"]) == 1


def test_the_leftovers_of_a_split_arrow_are_ignored_by_a_menu():
    # ESC, '[', 'B' arriving as three separate keys — the pty case.
    screen = FakeScreen([ESC, ord("["), ord("B"), DOWN, ENTER])

    assert menu(screen, "Pick", ["a", "b"]) == 1


def test_q_is_still_how_you_leave_a_menu():
    assert menu(FakeScreen([ord("q")]), "Pick", ["a"]) is None


def test_a_split_arrow_does_not_cancel_a_prompt_or_type_junk():
    """In a text field the same bytes must not cancel it, and must not land in
    the buffer as '[B' either."""
    screen = FakeScreen([ESC, ord("["), ord("B")] + _keys("cryptroot") + [ENTER])

    assert prompt(screen, "LUKS", "name", default="x") == "cryptroot"
