from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from dasik.lib.actions.pacman_repos_state import (RepoSection, parse_sections, third_party,
                                                   below_core, render, options_block,
                                                   section_of, _is_blank, _field, _body_end)

STOCK = Path("tests/fixtures/pacman_repos/pacman.conf.stock").read_text()
AMT = RepoSection("amt911", "Required", ("https://amt911.github.io/arch-packages/$arch",), None)


def test_stock_has_no_third_party():
    assert third_party(parse_sections(STOCK)) == []


def test_render_inserts_above_core_and_parses_back():
    out = render(STOCK, [AMT], remove=[])
    assert third_party(parse_sections(out)) == [AMT]
    assert out.index("[amt911]") < out.index("\n[core]")
    assert not below_core(out, "amt911")


def test_render_is_idempotent():
    once = render(STOCK, [AMT], remove=[])
    assert render(once, [AMT], remove=[]) == once


def test_render_remove_restores_stock_exactly():
    assert render(render(STOCK, [AMT], remove=[]), [], remove=["amt911"]) == STOCK


def test_hand_written_section_below_core_is_detected_and_moved():
    text = STOCK + "\n[amt911]\nSigLevel = Required\nServer = https://amt911.github.io/arch-packages/$arch\n"
    assert below_core(text, "amt911")
    out = render(text, [AMT], remove=[])
    assert not below_core(out, "amt911")
    assert out.count("[amt911]") == 1


def test_body_stops_at_comment_so_neighbouring_comments_survive():
    text = "[options]\n\n[mine]\nServer = file:///r\n#[core-testing]\n#Include = /etc/pacman.d/mirrorlist\n\n[core]\nInclude = /etc/pacman.d/mirrorlist\n"
    out = render(text, [], remove=["mine"])
    assert "#[core-testing]\n#Include = /etc/pacman.d/mirrorlist" in out
    assert "[mine]" not in out


def test_multiple_servers_and_include_parse():
    text = "[a]\nServer = https://x/$arch\nServer = https://y/$arch\n\n[b]\nInclude = /etc/pacman.d/b\n\n[core]\nInclude = /etc/pacman.d/mirrorlist\n"
    a, b = third_party(parse_sections(text))
    assert a.servers == ("https://x/$arch", "https://y/$arch") and a.sig_level is None
    assert b.include == "/etc/pacman.d/b" and b.servers == ()


def test_commented_header_is_not_a_section():
    assert third_party(parse_sections("#[amt911]\n#Server = https://x\n\n[core]\n")) == []


def test_unrelated_third_party_is_untouched_by_render():
    text = render(STOCK, [RepoSection("other", None, ("https://o/$arch",), None)], remove=[])
    out = render(text, [AMT], remove=[])
    assert [s.name for s in third_party(parse_sections(out))] == ["other", "amt911"]


_names = st.from_regex(r"[a-z][a-z0-9-]{0,8}", fullmatch=True).filter(
    lambda n: n not in {"core", "extra", "multilib", "options", "testing", "community"})


@given(st.lists(_names, unique=True, max_size=4))
def test_property_render_parse_roundtrip(names):
    declared = [RepoSection(n, "Required", (f"https://{n}.example/$arch",), None) for n in names]
    out = render(STOCK, declared, remove=[])
    assert third_party(parse_sections(out)) == declared
    assert render(out, declared, remove=[]) == out
    assert render(out, [], remove=names) == STOCK


# --- fix round 1: pacman trims whitespace/CRLF when classifying lines; render
# must reject a repeated declared name instead of silently duplicating it. ---

def test_render_inserts_above_core_with_trailing_spaces_in_header():
    text = STOCK.replace("[core]\n", "[core]  \n")
    assert "[core]  \n" in text  # sanity: the replace actually landed
    out = render(text, [AMT], remove=[])
    assert third_party(parse_sections(out)) == [AMT]
    assert out.index("[amt911]") < out.index("[core]")
    assert not below_core(out, "amt911")


X = RepoSection("x", None, ("https://x/$arch",), None)


def test_hand_written_section_with_odd_spacing_is_parsed_and_moved():
    text = STOCK + "\n  [x]  \n   Server = https://x/$arch\n"
    sections = [s for s in third_party(parse_sections(text)) if s.name == "x"]
    assert sections == [X]
    assert below_core(text, "x")
    out = render(text, [X], remove=[])
    assert not below_core(out, "x")
    assert out.count("[x]") == 1
    assert out.index("[x]") < out.index("[core]")


def test_crlf_input_is_parsed_and_rendered():
    text = ("[options]\r\n\r\n[mine]\r\nServer = https://m.example/$arch\r\n\r\n"
            "[core]\r\nInclude = /etc/pacman.d/mirrorlist\r\n")
    mine = RepoSection("mine", None, ("https://m.example/$arch",), None)
    assert third_party(parse_sections(text)) == [mine]
    out = render(text, [mine], remove=[])
    assert out.count("[mine]") == 1
    assert out.index("[mine]") < out.index("[core]")


def test_render_raises_on_duplicate_declared_name():
    with pytest.raises(ValueError, match="amt911"):
        render(STOCK, [AMT, AMT], remove=[])


# --- mutation-testing round: killers for pacman_repos_state.py survivors ---
# (pyproject.toml [tool.mutmut].only_mutate; see docs/mutation-testing.md). Each
# test below is paired 1:1 with a mutant diff confirmed, via a scratch harness
# replaying mutmut's own generated variants, to actually change behaviour for
# the given input — not just "looks like it should".

def test_is_blank_direct():
    assert _is_blank("")
    assert _is_blank("   ")
    assert _is_blank("\r")
    assert not _is_blank("x")


def test_field_reads_dict_key_or_default():
    assert _field({"a": 1}, "a", "default") == 1
    assert _field({"a": 1}, "b", "default") == "default"


def test_field_reads_attribute_or_default():
    class _Obj:
        pass
    obj = _Obj()
    obj.a = 1
    assert _field(obj, "a", "default") == 1
    assert _field(obj, "b", "default") == "default"


def test_body_stops_at_comment_with_no_key_lines_so_comment_survives_removal():
    # A section with NO key lines at all, immediately followed by a comment
    # (not a blank line): _body_end must stop AT that comment (not one line
    # past it), or render()'s removal range swallows the neighbouring comment.
    text = "[options]\n\n[mine]\n#a stray comment\n\n[core]\nInclude = /x\n"
    out = render(text, [], remove=["mine"])
    assert out == "[options]\n\n#a stray comment\n\n[core]\nInclude = /x\n"


def test_parse_sections_excludes_options_by_name_not_by_accident():
    assert "options" not in {s.name for s in parse_sections(STOCK)}


def test_parse_sections_skips_a_stray_non_key_line_and_keeps_reading():
    # A line inside a section's body that is neither a key line, a comment,
    # nor blank must be skipped (continue), not treated as the end of the
    # body — otherwise a later real key line in the same section is lost.
    text = "[a]\nSomeJunkLine\nServer = https://y\n\n[core]\n"
    sections = {s.name: s for s in parse_sections(text)}
    assert sections["a"].servers == ("https://y",)


def test_below_core_false_when_there_is_no_core_section_at_all():
    text = "[a]\nServer = https://a\n\n[b]\nServer = https://b\n"
    assert below_core(text, "b") is False


def test_below_core_false_for_a_name_that_does_not_exist_as_a_header():
    text = "[options]\n\n[a]\nServer = https://a\n\n[core]\nInclude = /x\n"
    assert below_core(text, "zzz") is False


def test_below_core_false_when_querying_core_itself():
    text = "[options]\n\n[a]\nServer = https://a\n\n[core]\nInclude = /x\n"
    assert below_core(text, "core") is False


def test_below_core_uses_the_first_core_occurrence_on_a_duplicate():
    # Malformed input with [core] declared twice: below_core must anchor on
    # the FIRST occurrence for both the core position and a same-named query,
    # so a query for "core" itself is still False (never "below itself").
    text = "[core]\nInclude=/y\n[core]\nInclude=/y\n"
    assert below_core(text, "core") is False


def test_below_core_two_headers_glued_on_one_physical_line_are_not_headers():
    # "[a] [b]" is ONE line containing two bracket pairs; the header grammar
    # (_HEADER_RE, applied to the whole stripped line) rejects it as a header
    # at all -- it must not be treated as two separate headers "a" and "b".
    text = "[core]\nInclude=/x\n\n[a] [b]\nServer=y\n"
    assert below_core(text, "b") is False


def test_options_block_stops_at_the_next_header_when_two_headers_exist():
    text = "[options]\nColor\n\n[core]\nInclude=/x\n"
    assert options_block(text) == "[options]\nColor\n"


def test_render_of_an_include_repo_does_not_crash_and_writes_include_line():
    section = RepoSection("chaotic-aur", None, (), "/etc/pacman.d/chaotic-mirrorlist")
    out = render(STOCK, [section], remove=[])
    assert "Include = /etc/pacman.d/chaotic-mirrorlist" in out


def test_render_separates_a_rendered_section_with_an_empty_line():
    section = RepoSection("amt911", "Required", ("https://x/$arch",), None)
    out = render(STOCK, [section], remove=[])
    assert "Server = https://x/$arch\n\n[core]" in out


def test_render_preserves_a_trailing_blank_line_after_removing_the_last_section():
    text = "[options]\n\n[core]\nInclude = /etc/pacman.d/mirrorlist\n\n[z]\nServer = https://z/$arch\n"
    out = render(text, [], remove=["z"])
    assert out == "[options]\n\n[core]\nInclude = /etc/pacman.d/mirrorlist\n\n"


def test_section_of_reads_include_field_from_a_dict():
    section = section_of({"name": "chaotic-aur", "include": "/etc/pacman.d/chaotic-mirrorlist"})
    assert section.include == "/etc/pacman.d/chaotic-mirrorlist"


# --- fix round 2: a section's body must reach its LAST key line, not stop at
# the FIRST interior comment/blank line — a hand-written section following
# the repo's own README (a comment right under the header) or with a blank
# line between two directives was torn: `render` moved only the header and
# left the key lines dangling where the header used to be, which pacman then
# reads as directives of whatever section precedes them. ---

AMT_SERVER = "https://amt911.github.io/arch-packages/$arch"


def test_hand_written_section_with_an_interior_comment_is_parsed_and_removed_whole():
    text = (STOCK + "\n[amt911]\n# my personal repo\nSigLevel = Required\n"
            f"Server = {AMT_SERVER}\n")

    sections = {s.name: s for s in third_party(parse_sections(text))}
    assert sections["amt911"].sig_level == "Required"
    assert sections["amt911"].servers == (AMT_SERVER,)

    declared = RepoSection("amt911", "Required", (AMT_SERVER,), None)
    out = render(text, [declared], remove=[])

    lines = out.split("\n")
    assert lines.count("[amt911]") == 1
    block_index = lines.index("[amt911]")
    assert block_index < lines.index("[core]")
    assert lines[block_index:block_index + 3] == [
        "[amt911]", "SigLevel = Required", f"Server = {AMT_SERVER}",
    ]
    # Nothing from the hand-written body may survive OUTSIDE that one
    # freshly-rendered block — this is what the old `_body_end` got wrong:
    # it left these lines dangling right where the original header was.
    remainder_lines = lines[:block_index] + lines[block_index + 3:]
    assert "# my personal repo" not in remainder_lines
    assert "SigLevel = Required" not in remainder_lines
    assert f"Server = {AMT_SERVER}" not in remainder_lines


def test_hand_written_section_with_a_blank_line_between_directives_keeps_both():
    text = (STOCK + "\n[amt911]\nSigLevel = Required\n\n"
            f"Server = {AMT_SERVER}\n")

    sections = {s.name: s for s in third_party(parse_sections(text))}
    assert sections["amt911"].sig_level == "Required"
    assert sections["amt911"].servers == (AMT_SERVER,)  # was () under the old _body_end

    declared = RepoSection("amt911", "Required", (AMT_SERVER,), None)
    out = render(text, [declared], remove=[])

    lines = out.split("\n")
    assert lines.count("[amt911]") == 1
    block_index = lines.index("[amt911]")
    assert block_index < lines.index("[core]")
    remainder_lines = lines[:block_index] + lines[block_index + 3:]
    assert "SigLevel = Required" not in remainder_lines
    assert f"Server = {AMT_SERVER}" not in remainder_lines


# --- mutation-testing round 2: killers for _body_end's rewrite (round 1's
# interior-comment fix) — 1:1 with mutmut diffs confirmed via
# `mutmut show <name>` after a `scripts/mutation.sh` run. ---


def test_body_end_boundary_search_checks_the_line_right_after_the_header():
    # Kills: `range(header_index + 1, total)` -> `range(header_index + 2, total)`
    # in the boundary-finding loop. Two ADJACENT headers (the first with no
    # body at all) means the boundary for the first header IS the very next
    # line; skipping that line (the mutant) makes the search miss it and run
    # on to a LATER header instead, letting the first section's last-key scan
    # wrongly absorb the second section's own Server line.
    text = "[options]\n\n[a]\n[b]\nServer = https://b\n\n[core]\nInclude = /x\n"
    sections = {s.name: s for s in third_party(parse_sections(text))}
    assert sections["a"].servers == ()
    assert sections["a"].sig_level is None
    assert sections["b"].servers == ("https://b",)


def test_body_end_direct_last_key_search_never_looks_before_the_header():
    # Kills: `range(header_index + 1, boundary)` -> `range(boundary)` (starts
    # at 0) in the last-key search. A key-looking line BEFORE the header,
    # with the header's own body genuinely empty, must never be picked up as
    # that header's last key line.
    lines = ["Server = should-not-count", "filler", "[mine]", "", "[core]"]
    assert _body_end(lines, header_index=2) == 3


def test_body_end_direct_last_key_search_never_starts_before_the_header_plus_one():
    # Kills: `range(header_index + 1, boundary)` -> `range(header_index - 1, boundary)`
    # (starts one line early, at the header's own predecessor). A key-looking
    # line immediately ABOVE the header — the previous section's own last
    # directive — must never be picked up as this header's last key line.
    lines = ["[options]", "Server = should-not-count", "[mine]", "", "[core]"]
    assert _body_end(lines, header_index=2) == 3
