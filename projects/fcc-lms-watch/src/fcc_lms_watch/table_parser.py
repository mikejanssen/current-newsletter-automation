from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser


def _clean(text: str) -> str:
    return " ".join(unescape(text).replace("\xa0", " ").split())


@dataclass
class TableState:
    in_target_table: bool = False
    in_thead: bool = False
    in_tbody: bool = False
    in_th: bool = False
    in_td: bool = False
    headers: list[str] = field(default_factory=list)
    current_header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    current_row: list[str] = field(default_factory=list)
    current_cell: list[str] = field(default_factory=list)


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.state = TableState()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k: v for k, v in attrs}
        if tag == "table" and attrs_dict.get("id") == "table-appsInbox":
            self.state.in_target_table = True
            return
        if not self.state.in_target_table:
            return
        if tag == "thead":
            self.state.in_thead = True
        elif tag == "tbody":
            self.state.in_tbody = True
        elif tag == "tr" and self.state.in_tbody:
            self.state.current_row = []
        elif tag == "th" and self.state.in_thead:
            self.state.in_th = True
            self.state.current_header = []
        elif tag == "td" and self.state.in_tbody:
            self.state.in_td = True
            self.state.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.state.in_th:
            self.state.current_header.append(data)
        elif self.state.in_td:
            self.state.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.state.in_target_table:
            self.state.in_target_table = False
            return
        if not self.state.in_target_table:
            return
        if tag == "thead":
            self.state.in_thead = False
        elif tag == "tbody":
            self.state.in_tbody = False
        elif tag == "th" and self.state.in_th:
            self.state.in_th = False
            value = _clean("".join(self.state.current_header))
            if value:
                self.state.headers.append(value)
        elif tag == "td" and self.state.in_td:
            self.state.in_td = False
            self.state.current_row.append(_clean("".join(self.state.current_cell)))
        elif tag == "tr" and self.state.in_tbody and self.state.current_row:
            self.state.rows.append(self.state.current_row)


def parse_results_table(html: str) -> list[dict[str, str]]:
    parser = TableParser()
    parser.feed(html)
    headers = parser.state.headers
    rows = parser.state.rows
    if not headers or not rows:
        return []

    normalized_headers = headers[:]
    if normalized_headers and normalized_headers[0].lower() == "pdf":
        normalized_headers[0] = "PDF"

    out: list[dict[str, str]] = []
    for row in rows:
        if len(row) < len(normalized_headers):
            row = row + [""] * (len(normalized_headers) - len(row))
        mapped = {normalized_headers[i]: row[i] for i in range(min(len(normalized_headers), len(row)))}
        out.append(mapped)
    return out
