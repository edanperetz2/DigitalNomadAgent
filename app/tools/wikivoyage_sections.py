"""Reusable revision-pinned Wikivoyage section retrieval."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

from app.tools.mediawiki_client import MediaWikiClient


class WikivoyageSectionNotFound(ValueError):
    """The destination article or requested section does not exist."""


@dataclass(frozen=True)
class WikivoyageSection:
    resolved_title: str
    page_id: int
    revision_id: int
    revision_timestamp: str
    section_title: str
    section_index: str
    section_anchor: str
    excerpt: str
    source_url: str

    def normalized_data(self) -> dict[str, str | int]:
        return {
            "resolved_title": self.resolved_title,
            "page_id": self.page_id,
            "revision_id": self.revision_id,
            "revision_timestamp": self.revision_timestamp,
            "section_title": self.section_title,
            "section_index": self.section_index,
            "section_anchor": self.section_anchor,
            "excerpt": self.excerpt,
        }


class _VisibleTextParser(HTMLParser):
    _SKIP_TAGS = {"style", "script", "figure", "sup"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "li", "br", "h2", "h3", "h4"} and not self._skip_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _payload_value(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return str(payload.get("*") or "")
    return ""


def _plain_text(rendered_html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(rendered_html)
    lines = [" ".join(html.unescape(line).split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\[\s*edit(?:\s*\|\s*edit source)?\s*\]", "", text, flags=re.I)


class WikivoyageSectionClient:
    """Resolve one article revision and return one named section from it."""

    def __init__(self, mediawiki: MediaWikiClient) -> None:
        self._mediawiki = mediawiki

    async def fetch(
        self,
        title: str,
        section_names: tuple[str, ...],
        *,
        max_excerpt_chars: int,
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
        excerpt = _plain_text(rendered_html)[:max_excerpt_chars].strip()
        if not excerpt:
            raise ValueError("Wikivoyage returned no readable text for the requested section")

        section_title = str(section.get("line") or section_names[0])
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
            excerpt=excerpt,
            source_url=source_url,
        )
