"""Reusable revision-pinned Wikivoyage section retrieval and context coverage."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

from app.tools.mediawiki_client import MediaWikiClient

DEFAULT_PREVIEW_CHARS = 600
DEFAULT_MAX_CONTEXT_CHARS = 20_000
DEFAULT_MAX_CHUNK_CHARS = 2_000
CONTEXT_CONTRACT_VERSION = 1


class WikivoyageSectionNotFound(ValueError):
    """The destination article or requested section does not exist."""


@dataclass(frozen=True)
class WikivoyageContextChunk:
    subsection: str
    text: str
    subsection_truncated: bool = False

    def normalized_data(self) -> dict[str, str | bool]:
        return {
            "subsection": self.subsection,
            "text": self.text,
            "subsection_truncated": self.subsection_truncated,
        }


@dataclass(frozen=True)
class WikivoyageSectionContext:
    preview_excerpt: str
    context_chunks: tuple[WikivoyageContextChunk, ...]
    full_section_chars: int
    included_chars: int
    truncated: bool
    included_subsections: tuple[str, ...]
    truncated_subsections: tuple[str, ...]
    omitted_subsections: tuple[str, ...]

    def normalized_data(self) -> dict[str, Any]:
        return {
            "preview_excerpt": self.preview_excerpt,
            "excerpt": self.preview_excerpt,
            "context_chunks": [chunk.normalized_data() for chunk in self.context_chunks],
            "full_section_chars": self.full_section_chars,
            "included_chars": self.included_chars,
            "truncated": self.truncated,
            "included_subsections": list(self.included_subsections),
            "truncated_subsections": list(self.truncated_subsections),
            "omitted_subsections": list(self.omitted_subsections),
        }


@dataclass(frozen=True)
class WikivoyageSection:
    resolved_title: str
    page_id: int
    revision_id: int
    revision_timestamp: str
    section_title: str
    section_index: str
    section_anchor: str
    context: WikivoyageSectionContext
    source_url: str

    @property
    def excerpt(self) -> str:
        """Compatibility preview; reasoning consumers should use context chunks."""
        return self.context.preview_excerpt

    def normalized_data(self) -> dict[str, Any]:
        return {
            "resolved_title": self.resolved_title,
            "page_id": self.page_id,
            "revision_id": self.revision_id,
            "revision_timestamp": self.revision_timestamp,
            "section_title": self.section_title,
            "section_index": self.section_index,
            "section_anchor": self.section_anchor,
            **self.context.normalized_data(),
        }


@dataclass(frozen=True)
class _SectionGroup:
    subsection: str
    text: str


class _SectionBlockParser(HTMLParser):
    _SKIP_TAGS = {"style", "script", "figure", "sup"}
    _HEADING_TAGS = {"h2", "h3", "h4", "h5", "h6"}
    _TEXT_BLOCK_TAGS = {"p", "li", "tr"}

    def __init__(self, section_title: str, *, skip_tables: bool) -> None:
        super().__init__()
        self._skip_tags = self._SKIP_TAGS | ({"table"} if skip_tables else set())
        self._skip_depth = 0
        self._active_tag: str | None = None
        self._active_kind: str | None = None
        self._parts: list[str] = []
        self._current_subsection = section_title
        self.blocks: list[tuple[str, str]] = []

    @staticmethod
    def _clean(value: str) -> str:
        cleaned = " ".join(html.unescape(value).split())
        return re.sub(r"\[\s*edit(?:\s*\|\s*edit source)?\s*\]", "", cleaned, flags=re.I).strip()

    def _flush(self) -> None:
        text = self._clean("".join(self._parts))
        if text:
            if self._active_kind == "heading":
                self._current_subsection = text
            else:
                self.blocks.append((self._current_subsection, text))
        self._active_tag = None
        self._active_kind = None
        self._parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._skip_tags:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._HEADING_TAGS:
            self._flush()
            self._active_tag = tag
            self._active_kind = "heading"
        elif tag in self._TEXT_BLOCK_TAGS and self._active_tag is None:
            self._active_tag = tag
            self._active_kind = "text"
        elif tag == "br" and self._active_tag is not None:
            self._parts.append(" ")
        elif tag == "td" and self._active_tag == "tr":
            self._parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == self._active_tag:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and self._active_tag is not None:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _group_blocks(blocks: list[tuple[str, str]]) -> list[_SectionGroup]:
    groups: list[_SectionGroup] = []
    for subsection, text in blocks:
        if groups and groups[-1].subsection == subsection:
            previous = groups[-1]
            groups[-1] = _SectionGroup(subsection=subsection, text=f"{previous.text}\n{text}")
        else:
            groups.append(_SectionGroup(subsection=subsection, text=text))
    return groups


def _allocate_context(groups: list[_SectionGroup], max_chars: int) -> list[int]:
    if max_chars <= 0:
        raise ValueError("max_context_chars must be positive")
    if not groups:
        return []
    lengths = [len(group.text) for group in groups]
    if sum(lengths) <= max_chars:
        return lengths

    equal_share = max_chars // len(groups)
    allocations = [min(length, equal_share) for length in lengths]
    remaining = max_chars - sum(allocations)
    while remaining:
        expandable = [index for index, length in enumerate(lengths) if allocations[index] < length]
        if not expandable:
            break
        share = max(1, remaining // len(expandable))
        progressed = 0
        for index in expandable:
            addition = min(share, lengths[index] - allocations[index], remaining)
            allocations[index] += addition
            remaining -= addition
            progressed += addition
            if remaining == 0:
                break
        if progressed == 0:
            break
    return allocations


def _split_chunks(text: str, max_chars: int) -> list[str]:
    if max_chars <= 0:
        raise ValueError("max_chunk_chars must be positive")
    remaining = text.strip()
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_chars + 1)
        if cut < max_chars // 2:
            cut = remaining.rfind(". ", 0, max_chars + 1)
            if cut >= max_chars // 2:
                cut += 1
        if cut < max_chars // 2:
            cut = remaining.rfind(" ", 0, max_chars + 1)
        if cut < max_chars // 2:
            cut = max_chars
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [chunk for chunk in chunks if chunk]


def _ordered_unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def build_section_context(
    rendered_html: str,
    section_title: str,
    *,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    skip_tables: bool = False,
) -> WikivoyageSectionContext:
    """Preserve broad subsection coverage under one bounded reasoning budget."""
    if preview_chars <= 0:
        raise ValueError("preview_chars must be positive")
    parser = _SectionBlockParser(section_title, skip_tables=skip_tables)
    parser.feed(rendered_html)
    parser.close()
    groups = _group_blocks(parser.blocks)
    if not groups:
        raise ValueError("Wikivoyage returned no readable text for the requested section")

    full_text = "\n".join(group.text for group in groups)
    full_section_chars = sum(len(group.text) for group in groups)
    allocations = _allocate_context(groups, max_context_chars)
    chunks: list[WikivoyageContextChunk] = []
    included_subsections: list[str] = []
    truncated_subsections: list[str] = []
    omitted_subsections: list[str] = []
    included_chars = 0
    for group, allocation in zip(groups, allocations, strict=True):
        if allocation <= 0:
            omitted_subsections.append(group.subsection)
            continue
        selected = group.text[:allocation].rstrip()
        if not selected:
            omitted_subsections.append(group.subsection)
            continue
        group_truncated = len(selected) < len(group.text)
        included_subsections.append(group.subsection)
        if group_truncated:
            truncated_subsections.append(group.subsection)
        included_chars += len(selected)
        chunks.extend(
            WikivoyageContextChunk(
                subsection=group.subsection,
                text=chunk,
                subsection_truncated=group_truncated,
            )
            for chunk in _split_chunks(selected, max_chunk_chars)
        )

    return WikivoyageSectionContext(
        preview_excerpt=full_text[:preview_chars].strip(),
        context_chunks=tuple(chunks),
        full_section_chars=full_section_chars,
        included_chars=included_chars,
        truncated=included_chars < full_section_chars,
        included_subsections=_ordered_unique(included_subsections),
        truncated_subsections=_ordered_unique(truncated_subsections),
        omitted_subsections=_ordered_unique(omitted_subsections),
    )


class WikivoyageSectionClient:
    """Resolve one article revision and return one named, coverage-aware section."""

    def __init__(self, mediawiki: MediaWikiClient) -> None:
        self._mediawiki = mediawiki

    async def fetch(
        self,
        title: str,
        section_names: tuple[str, ...],
        *,
        preview_chars: int = DEFAULT_PREVIEW_CHARS,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
        skip_tables: bool = False,
    ) -> WikivoyageSection:
        payload = await self._mediawiki.request(
            action="query",
            prop="revisions",
            rvprop="ids|timestamp",
            titles=title,
            redirects=1,
        )
        pages = (payload.get("query") or {}).get("pages") or []
        if isinstance(pages, dict):
            pages = list(pages.values())
        page = next((item for item in pages if isinstance(item, dict) and "missing" not in item), None)
        if page is None:
            raise WikivoyageSectionNotFound("No Wikivoyage article was found for this destination")
        if not page.get("pageid") or not page.get("title"):
            raise ValueError("Wikivoyage did not return page identity for this article")
        revisions = page.get("revisions") or []
        revision = revisions[0] if revisions else {}
        if not revision.get("revid") or not revision.get("timestamp"):
            raise ValueError("Wikivoyage did not return revision identity for this article")

        resolved_title = str(page["title"])
        page_id = int(page["pageid"])
        revision_id = int(revision["revid"])
        revision_timestamp = str(revision["timestamp"])
        sections_payload = await self._mediawiki.request(
            action="parse",
            oldid=revision_id,
            prop="sections",
        )
        sections = (sections_payload.get("parse") or {}).get("sections") or []
        if not isinstance(sections, list):
            raise ValueError("Wikivoyage returned malformed section metadata")
        wanted = {" ".join(name.casefold().split()) for name in section_names}
        section = next(
            (
                item
                for item in sections
                if isinstance(item, dict)
                and " ".join(str(item.get("line") or "").casefold().split()) in wanted
            ),
            None,
        )
        if section is None:
            joined = " or ".join(section_names)
            raise WikivoyageSectionNotFound(f"The Wikivoyage article has no {joined} section")
        if "index" not in section:
            raise ValueError("Wikivoyage did not return a section index")

        section_payload = await self._mediawiki.request(
            action="parse",
            oldid=revision_id,
            section=str(section["index"]),
            prop="text|revid",
        )
        parsed = section_payload.get("parse") or {}
        if int(parsed.get("revid") or 0) != revision_id:
            raise ValueError("Wikivoyage returned an unexpected revision for the requested section")
        rendered_html = _payload_value(parsed.get("text"))
        if not rendered_html:
            raise ValueError("Wikivoyage returned an empty requested section")

        section_title = str(section.get("line") or section_names[0])
        context = build_section_context(
            rendered_html,
            section_title,
            preview_chars=preview_chars,
            max_context_chars=max_context_chars,
            max_chunk_chars=max_chunk_chars,
            skip_tables=skip_tables,
        )
        section_anchor = str(section.get("anchor") or section_title)
        source_url = (
            f"https://en.wikivoyage.org/w/index.php?oldid={revision_id}#{quote(section_anchor)}"
        )
        return WikivoyageSection(
            resolved_title=resolved_title,
            page_id=page_id,
            revision_id=revision_id,
            revision_timestamp=revision_timestamp,
            section_title=section_title,
            section_index=str(section["index"]),
            section_anchor=section_anchor,
            context=context,
            source_url=source_url,
        )


def _payload_value(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return str(payload.get("*") or "")
    return ""
