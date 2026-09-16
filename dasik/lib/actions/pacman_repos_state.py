"""Parse and render third-party ``pacman.conf`` repository sections.

Pure text logic, no file IO and no ``Command`` — the later ``PacmanRepositoriesAction``
(a different module) reads/writes the real ``/mnt/etc/pacman.conf`` and calls into
here to decide what changed and what to write back, exactly the way
``mcp_servers_state.py`` normalizes two registries for ``mcp_servers_action.py``
to compare and act on.

A ``pacman.conf`` repository section looks like::

    [amt911]
    SigLevel = Required
    Server = https://amt911.github.io/arch-packages/$arch

The header must be **uncommented** (a line that, once leading/trailing
whitespace is stripped, reads exactly ``[name]``, not ``#[name]``) to count as
a real section — a commented-out header such as the stock file's
``#[core-testing]`` is inert to pacman and must be inert here too. A section's
body is its **contiguous** key lines right after the header: the first blank
line, comment line, or next header line ends it. That stop rule is what lets
``render`` remove one section without disturbing a neighbouring commented-out
block like ``#[core-testing]`` two lines below it.

Every line is classified (header / comment / blank / key) after stripping its
leading/trailing whitespace — which also strips a trailing ``\r``, since
``pacman-conf`` itself accepts ``  [name]  ``, an indented ``   Server =
...``, and CRLF line endings and parses all of them the same as their
untrimmed forms (measured against the real binary, not assumed). A removed
section's *original* lines are deleted whatever their spacing; a
freshly-inserted ``declared`` block is always written out canonical (plain
``\n``, no leading whitespace). Everything else in the file — including an
untouched line's original spacing — is byte-identical on the way out.

``render`` always re-homes the sections it manages (``declared``) immediately
above the ``[core]`` header — a hand-edited section anywhere else, even one
below ``[core]``, gets picked up by name and moved back into place — because
that is the one spot a *third-party* repo is guaranteed not to collide with a
future official section pacman adds after ``[options]``. ``declared`` must not
repeat a name — that would silently render as two ``[name]`` blocks — so
``render`` raises ``ValueError`` naming the duplicate instead (the model
already refuses this upstream; ``render`` enforces its own precondition rather
than trusting the caller).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Tuple

from ..models.pacman_model import OFFICIAL_REPOS

# A real header: a line that, once stripped of leading/trailing whitespace, is
# *exactly* "[name]" — no leading "#" (which would make it a comment pacman
# ignores), no trailing junk. Applied to the stripped line, never the raw one,
# so "  [x]  " and "[x]\r" (CRLF) are headers too, matching pacman-conf(5).
_HEADER_RE = re.compile(r"^\[([^\[\]]+)\]$")

# The three directives a repository section carries. Values are taken
# verbatim (whitespace-trimmed) — dasik never rewrites what a value means,
# only where the section lives. Applied to the stripped line, so an indented
# "   Server = ..." is recognised the same as an unindented one.
_KEY_RE = re.compile(r"^(SigLevel|Server|Include)\s*=\s*(.*)$")


def _is_blank(line: str) -> bool:
    """A line that is empty once whitespace (incl. a trailing ``\\r``) is stripped."""
    return line.strip() == ""


def _is_comment(line: str) -> bool:
    """A line whose first non-whitespace character is ``#``."""
    return line.strip().startswith("#")


def _match_header(line: str) -> Optional["re.Match[str]"]:
    """``_HEADER_RE`` applied to the line's stripped content."""
    return _HEADER_RE.match(line.strip())


@dataclass(frozen=True)
class RepoSection:
    """One ``pacman.conf`` repository section, official or third-party."""

    name: str
    sig_level: Optional[str]
    servers: Tuple[str, ...]
    include: Optional[str]


def _field(entry: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a dict or from a pydantic model, whichever arrived."""
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def _iter_headers(lines: List[str]) -> List[Tuple[int, str]]:
    """Every uncommented header's ``(line index, name)``, in file order."""
    headers = []
    for index, line in enumerate(lines):
        match = _match_header(line)
        if match:
            headers.append((index, match.group(1)))
    return headers


def _body_end(lines: List[str], header_index: int) -> int:
    """Index (exclusive) where *header_index*'s contiguous key lines stop.

    Stops at the first blank line, comment line, or next header — whichever
    comes first — so a commented-out neighbour is never absorbed into the
    section above it.
    """
    index = header_index + 1
    total = len(lines)
    while index < total:
        line = lines[index]
        if _is_blank(line) or _is_comment(line) or _match_header(line):
            break
        index += 1
    return index


def parse_sections(text: str) -> List[RepoSection]:
    """Every uncommented ``[name]`` section except ``[options]``, in file order."""
    lines = text.split("\n")
    sections = []
    for header_index, name in _iter_headers(lines):
        if name == "options":
            continue
        body_end = _body_end(lines, header_index)
        sig_level: Optional[str] = None
        servers: List[str] = []
        include: Optional[str] = None
        for line in lines[header_index + 1:body_end]:
            match = _KEY_RE.match(line.strip())
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip()
            if key == "SigLevel":
                sig_level = value
            elif key == "Server":
                servers.append(value)
            elif key == "Include":
                include = value
        sections.append(RepoSection(name, sig_level, tuple(servers), include))
    return sections


def third_party(sections: List[RepoSection]) -> List[RepoSection]:
    """Drop the official repos (and ``options``, already excluded by ``parse_sections``)."""
    return [section for section in sections if section.name not in OFFICIAL_REPOS]


def below_core(text: str, name: str) -> bool:
    """True only when both ``[core]`` and ``[name]`` exist, and ``name`` follows ``[core]``."""
    lines = text.split("\n")
    core_index: Optional[int] = None
    name_index: Optional[int] = None
    for header_index, header_name in _iter_headers(lines):
        if header_name == "core" and core_index is None:
            core_index = header_index
        if header_name == name and name_index is None:
            name_index = header_index
    if core_index is None or name_index is None:
        return False
    return name_index > core_index


def _render_block(section: RepoSection) -> List[str]:
    """``[name]`` + directive lines + one trailing blank line."""
    lines = [f"[{section.name}]"]
    if section.sig_level is not None:
        lines.append(f"SigLevel = {section.sig_level}")
    if section.include is not None:
        lines.append(f"Include = {section.include}")
    else:
        for server in section.servers:
            lines.append(f"Server = {server}")
    lines.append("")
    return lines


def render(text: str, declared: List[RepoSection], remove: Iterable[str]) -> str:
    """Remove ``declared`` ∪ ``remove`` by name, then reinsert ``declared`` above ``[core]``.

    Removal takes the header, its contiguous key lines, and at most one
    following blank line — never more, so a neighbouring blank line that was
    already part of the surrounding file structure survives. Every occurrence
    of a removed name is deleted (a hand-edited duplicate included), whatever
    that occurrence's original spacing. Sections dasik neither declares nor is
    told to remove are untouched, wherever they live in the file and however
    they are spaced.

    Raises ``ValueError`` if ``declared`` repeats a name — that would silently
    render as two ``[name]`` blocks in the same file.
    """
    seen_names: set = set()
    for section in declared:
        if section.name in seen_names:
            raise ValueError(
                f"pacman render: duplicate declared repository name {section.name!r}"
            )
        seen_names.add(section.name)

    trailing_newline = text.endswith("\n")
    lines = text.split("\n")
    if trailing_newline:
        lines = lines[:-1]

    names_to_remove = {section.name for section in declared} | set(remove)

    ranges: List[Tuple[int, int]] = []
    for header_index, name in _iter_headers(lines):
        if name not in names_to_remove:
            continue
        body_end = _body_end(lines, header_index)
        end = body_end
        if end < len(lines) and _is_blank(lines[end]):
            end += 1
        ranges.append((header_index, end))
    ranges.sort()

    kept: List[str] = []
    index = 0
    range_iter = iter(ranges)
    current = next(range_iter, None)
    while index < len(lines):
        if current is not None and index == current[0]:
            index = current[1]
            current = next(range_iter, None)
            continue
        kept.append(lines[index])
        index += 1

    block_lines: List[str] = []
    for section in declared:
        block_lines.extend(_render_block(section))

    core_index: Optional[int] = None
    for header_index, name in _iter_headers(kept):
        if name == "core":
            core_index = header_index
            break

    if core_index is None:
        result_lines = kept + block_lines
    else:
        result_lines = kept[:core_index] + block_lines + kept[core_index:]

    result = "\n".join(result_lines)
    if trailing_newline:
        result += "\n"
    return result


def section_of(model: Any) -> RepoSection:
    """Build a ``RepoSection`` from a ``PacmanRepositoryModel`` or its plain dict."""
    servers = tuple(_field(model, "servers", []) or [])
    return RepoSection(
        name=_field(model, "name"),
        sig_level=_field(model, "sig_level"),
        servers=servers,
        include=_field(model, "include"),
    )
