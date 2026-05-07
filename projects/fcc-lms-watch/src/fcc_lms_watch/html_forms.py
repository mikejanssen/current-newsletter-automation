from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from urllib.parse import urljoin


@dataclass
class Form:
    name: str | None
    form_id: str | None
    inputs: dict[str, str] = field(default_factory=dict)


class FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[Form] = []
        self._current: Form | None = None
        self._current_select: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k: v for k, v in attrs}
        if tag.lower() == "form":
            self._current = Form(name=attrs_dict.get("name"), form_id=attrs_dict.get("id"))
            self.forms.append(self._current)
            return
        if self._current is None:
            return
        if tag.lower() == "input":
            name = attrs_dict.get("name")
            if not name:
                return
            value = attrs_dict.get("value") or ""
            self._current.inputs[name] = value
        if tag.lower() == "select":
            name = attrs_dict.get("name")
            if not name:
                return
            if name not in self._current.inputs:
                self._current.inputs[name] = ""
            self._current_select = name
        if tag.lower() == "option" and self._current_select:
            if "selected" in attrs_dict:
                value = attrs_dict.get("value") or ""
                self._current.inputs[self._current_select] = value

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._current = None
        if tag.lower() == "select":
            self._current_select = None


def parse_forms(html: str) -> list[Form]:
    parser = FormParser()
    parser.feed(html)
    return parser.forms


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class TableRowsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_target_table = False
        self._in_tr = False
        self._in_th = False
        self._in_td = False
        self._cell_buf: list[str] = []
        self._cell_href: str | None = None
        self._header_cells: list[str] = []
        self._row_cells: list[str] = []
        self._row_links: list[str] = []
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self.row_links: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k: v for k, v in attrs}
        tag = tag.lower()
        if tag == "table":
            table_id = attrs_dict.get("id") or ""
            table_class = attrs_dict.get("class") or ""
            if "table-apps" in table_id or "table-apps" in table_class:
                self._in_target_table = True
            return
        if not self._in_target_table:
            return
        if tag == "tr":
            self._in_tr = True
            self._header_cells = []
            self._row_cells = []
            self._row_links = []
            return
        if not self._in_tr:
            return
        if tag == "th":
            self._in_th = True
            self._cell_buf = []
            return
        if tag == "td":
            self._in_td = True
            self._cell_buf = []
            self._cell_href = None
            return
        if tag == "a" and self._in_td and self._cell_href is None:
            href = attrs_dict.get("href")
            if href:
                self._cell_href = href.strip()
            return
        if (self._in_th or self._in_td) and tag == "br":
            self._cell_buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table" and self._in_target_table:
            self._in_target_table = False
            return
        if not self._in_target_table:
            return
        if tag == "th" and self._in_th:
            self._in_th = False
            self._header_cells.append(_clean_text("".join(self._cell_buf)))
            return
        if tag == "td" and self._in_td:
            self._in_td = False
            self._row_cells.append(_clean_text("".join(self._cell_buf)))
            self._row_links.append(self._cell_href or "")
            return
        if tag == "tr" and self._in_tr:
            self._in_tr = False
            if self._header_cells and not self.headers:
                self.headers = self._header_cells
            elif self._row_cells:
                self.rows.append(self._row_cells)
                self.row_links.append(self._row_links)

    def handle_data(self, data: str) -> None:
        if self._in_target_table and (self._in_th or self._in_td):
            self._cell_buf.append(data)


def parse_results_table_rows(html: str) -> list[dict[str, str]]:
    parser = TableRowsParser()
    parser.feed(html)
    if not parser.headers or not parser.rows:
        return []
    out: list[dict[str, str]] = []
    for row_idx, row in enumerate(parser.rows):
        mapped: dict[str, str] = {}
        row_links = parser.row_links[row_idx] if row_idx < len(parser.row_links) else []
        for idx, value in enumerate(row):
            key = parser.headers[idx] if idx < len(parser.headers) else f"Column {idx+1}"
            mapped[key] = value
            if idx < len(row_links) and row_links[idx]:
                absolute = urljoin("https://enterpriseefiling.fcc.gov", row_links[idx])
                lowered = key.lower()
                if lowered in {"file number", "lead file number", "member file number", "application id"}:
                    mapped["Detail URL"] = absolute
                elif lowered == "pdf":
                    mapped["PDF URL"] = absolute
        out.append(mapped)
    return out


def parse_public_notice_date_links(html: str) -> list[str]:
    links = re.findall(r'href=["\']([^"\']*publicNoticeSearchResult\.html\?pnDate=[^"\']+)["\']', html)
    out: list[str] = []
    seen: set[str] = set()
    for link in links:
        absolute = urljoin("https://enterpriseefiling.fcc.gov", link)
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out
