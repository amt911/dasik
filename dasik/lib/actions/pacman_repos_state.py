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
body runs from just after the header up to and including its **last** key
line (``SigLevel``/``Server``/``Include``) before the next header (or end of
file) — never further. An interior comment or blank line **between** two key
lines is part of the body (a hand-written section following the repo's own
README often opens with a comment right under the header, or has a blank
line between directives); a trailing comment or blank line **after** the
last key line is not, which is what lets ``render`` remove one section
without disturbing a neighbouring commented-out block like
``#[core-testing]`` two lines below it. A section with no key lines at all
has no body (its end equals its header's own index plus one): a lone
comment is not a directive to keep.

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
from typing import Any, Iterable, List, Optional, Set, Tuple

from .config_access import field as _field
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


def _iter_headers(lines: List[str]) -> List[Tuple[int, str]]:
    """Every uncommented header's ``(line index, name)``, in file order."""
    headers = []
    for index, line in enumerate(lines):
        match = _match_header(line)
        if match:
            headers.append((index, match.group(1)))
    return headers


def _body_end(lines: List[str], header_index: int) -> int:
    """Index (exclusive) where *header_index*'s body ends: right after its
    LAST key line before the next header (or end of file).

    An interior comment/blank line between two key lines is inside this
    range (and simply skipped by callers that only look for ``_KEY_RE``
    matches); a trailing comment/blank line after the last key line is not,
    so a commented-out neighbour like ``#[core-testing]`` is never absorbed
    into the section above it. A section with no key lines at all has no
    body: this returns ``header_index + 1``.
    """
    total = len(lines)
    boundary = total
    for index in range(header_index + 1, total):
        if _match_header(lines[index]):
            boundary = index
            break
    last_key = header_index
    for index in range(header_index + 1, boundary):
        if _KEY_RE.match(lines[index].strip()):
            last_key = index
    return last_key + 1


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


def options_block(text: str) -> str:
    """The ``[options]`` section of *text*, verbatim: its header through the
    line before the next uncommented header, or through the end of the file
    if ``[options]`` is the last (or only) section. ``""`` if there is no
    ``[options]`` header at all.

    Used by ``PacmanRepositoriesAction.apply`` to build the single-repo
    ``pacman.conf`` for ``pacman -Sy --config <temp>`` (FACT-PR-4,
    docs/FACTS.md): that temp file must carry the real ``[options]``
    verbatim (``Architecture``, ``SigLevel``, ...) plus exactly one
    repository section — extracting by "next uncommented header" (not "next
    ``[core]``") means it still isolates ``[options]`` correctly even after a
    declared section has just been re-homed directly above ``[core]``, ahead
    of it in the file.
    """
    lines = text.split("\n")
    headers = _iter_headers(lines)
    start: Optional[int] = None
    end = len(lines)
    for index, (header_index, name) in enumerate(headers):
        if name == "options":
            start = header_index
            if index + 1 < len(headers):
                end = headers[index + 1][0]
            break
    if start is None:
        return ""
    return "\n".join(lines[start:end])


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


# --- gpg --with-colons keyring parsing (pure) ------------------------------
#
# The functions below parse the *raw* ``gpg --with-colons`` output for the
# pacman keyring (``/etc/pacman.d/gnupg``), never ``pacman-key``'s own
# output: FACT-PR-1 (docs/FACTS.md) measured the two differ in exit code and
# in output shape (``pacman-key`` prints no parseable colon data at all on
# an absent key). They also parse the ``*-trusted`` files a keyring package
# ships (FACT-PR-5, ``<40-hex-fingerprint>:4:`` per line).
#
# "Trusted" here means *locally* signed by the keyring's own master key
# (what ``pacman-key --lsign-key`` does), not merely valid through the web
# of trust. FACT-PR-2 measured that the plain validity field (2nd column of
# a ``pub:`` record) reaches ``f`` for an ordinary signed-by-someone-else
# key too — on a real keyring every Arch packager key would look "trusted"
# by that column alone. The reliable signal is a ``sig:`` record whose class
# (field 11) carries the trailing ``l`` (LOCAL/non-exportable) and whose
# issuer key id (field 5) is one of the keyring's own secret ("master") key
# ids — never a self-signature (``13x``/``18x``, both exportable and issued
# by the key on itself).


def _colon_fields(line: str) -> List[str]:
    """Split one ``gpg --with-colons`` line into its ``:``-separated fields."""
    return line.rstrip("\r").split(":")


def _field_at(fields: List[str], index: int) -> str:
    """``fields[index]``, or ``""`` when the line was too short to have it.

    Guards against short/garbage lines (stderr noise, a stray ``tru:``
    record) that happen to share a prefix but not the full field count.
    """
    return fields[index] if len(fields) > index else ""


def primary_fingerprints(colons: str) -> Set[str]:
    """Every primary-key fingerprint in *colons*, uppercased.

    A primary fingerprint is the ``fpr`` record that directly follows a
    ``pub`` record (field 10). A ``fpr`` record following a ``sub`` (subkey)
    record never counts — tracked via the type of the immediately preceding
    record, which only a ``pub`` line arms.
    """
    fingerprints: Set[str] = set()
    last_type: Optional[str] = None
    for line in colons.split("\n"):
        if not line:
            last_type = None
            continue
        fields = _colon_fields(line)
        record_type = fields[0]
        if record_type == "fpr" and last_type == "pub":
            fingerprint = _field_at(fields, 9)
            if fingerprint:
                fingerprints.add(fingerprint.upper())
        last_type = record_type
    return fingerprints


def secret_keyids(colons: str) -> Set[str]:
    """Long key ids (field 5, uppercased) of every ``sec`` record.

    Parses ``gpg --list-secret-keys --with-colons`` output. In the pacman
    keyring this is the "Pacman Keyring Master Key" used to locally sign
    (``pacman-key --lsign-key``) every key it trusts.
    """
    keyids: Set[str] = set()
    for line in colons.split("\n"):
        if not line:
            continue
        fields = _colon_fields(line)
        if fields[0] == "sec":
            keyid = _field_at(fields, 4)
            if keyid:
                keyids.add(keyid.upper())
    return keyids


def trusted_fingerprints(colons: str, master_keyids: Set[str]) -> Set[str]:
    """Primary fingerprints carrying a LOCAL signature by ``master_keyids``.

    Parses ``gpg --list-sigs --with-colons`` output. A ``sig`` record
    belongs to the most recent ``pub`` record above it — sigs appear nested
    under that ``pub``'s ``uid`` (and, for a self-signature on a subkey,
    under its ``sub``) but neither resets the current fingerprint, only the
    next ``pub`` record does. Only a signature whose class (field 11) ends
    in ``l`` (local/non-exportable — what ``pacman-key --lsign-key``
    produces) AND whose issuer key id (field 5, compared uppercase) is in
    ``master_keyids`` counts. Self-signatures (``13x``, ``18x``) are issued
    by the key on itself and are exportable, so they never satisfy this.
    """
    master_upper = {keyid.upper() for keyid in master_keyids}
    trusted: Set[str] = set()
    current_fpr: Optional[str] = None
    last_type: Optional[str] = None
    for line in colons.split("\n"):
        if not line:
            last_type = None
            continue
        fields = _colon_fields(line)
        record_type = fields[0]
        if record_type == "pub":
            current_fpr = None
        elif record_type == "fpr" and last_type == "pub":
            fingerprint = _field_at(fields, 9)
            current_fpr = fingerprint.upper() if fingerprint else None
        elif record_type == "sig" and current_fpr is not None:
            sig_class = _field_at(fields, 10)
            issuer = _field_at(fields, 4).upper()
            if sig_class.endswith("l") and issuer in master_upper:
                trusted.add(current_fpr)
        last_type = record_type
    return trusted


def packaged_trusted(texts: Iterable[str]) -> Set[str]:
    """Fingerprints declared by ``*-trusted`` keyring files, uppercased.

    Each line is ``<40-hex-fingerprint>:4:`` (FACT-PR-5). A blank line or a
    line without a ``:`` is ignored.
    """
    fingerprints: Set[str] = set()
    for text in texts:
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped or ":" not in stripped:
                continue
            fingerprint = stripped.split(":", 1)[0]
            if fingerprint:
                fingerprints.add(fingerprint.upper())
    return fingerprints
