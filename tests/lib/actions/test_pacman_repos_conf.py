from pathlib import Path

from hypothesis import given, strategies as st

from dasik.lib.actions.pacman_repos_state import (RepoSection, parse_sections, third_party,
                                                   below_core, render)

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
