from __future__ import annotations

from dataclasses import dataclass


ASSIGNMENT_SEARCH_URL = "https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicAssignmentTransferSearch.html"
ASSIGNMENT_RESULTS_URL = "https://enterpriseefiling.fcc.gov/dataentry/public/tv/assignmentTransferSearchResults.html"
APPLICATION_SEARCH_URL = "https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicAppSearch.html"
APPLICATION_RESULTS_URL = "https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicAppSearchResults.html"
PUBLIC_SEARCH_LANDING_URL = "https://enterpriseefiling.fcc.gov/dataentry/public/tv/publicSearchLanding.html"

DEFAULT_LOOKBACK_DAYS = 3

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)

KEYWORDS_STA = [
    "sta",
    "silent",
    "silence",
    "suspension of operation",
    "resumption",
    "emergency",
]

KEYWORDS_CP = [
    "construction permit",
    "cp",
    "license to cover",
    "302",
    "340",
]

KEYWORDS_MINOR_MOD = [
    "minor modification",
    "minor mod",
    "modification of license",
]

KEYWORDS_ASSIGNMENT_TRANSFER = [
    "assignment of authorization",
    "assignment",
    "transfer of control",
    "transfer",
    "asset acquisition",
]

@dataclass(frozen=True)
class SearchFormConfig:
    url: str
    results_url: str
    submit_name: str
    date_from_name: str
    date_to_name: str
    field_names: list[str]


ASSIGNMENT_FORM = SearchFormConfig(
    url=ASSIGNMENT_SEARCH_URL,
    results_url=ASSIGNMENT_RESULTS_URL,
    submit_name="j_idt45",
    date_from_name="txt-fromDate",
    date_to_name="txt-toDate",
    field_names=[
        "frm-advSearch",
        "txt-purpose",
        "txt-callSign",
        "txt-facilityID",
        "txt-service",
        "txt-leadFileNum",
        "txt-memberFileNum",
        "txt-assigneeName",
        "txt-assignorName",
        "selct-status",
    ],
)

APPLICATION_FORM = SearchFormConfig(
    url=APPLICATION_SEARCH_URL,
    results_url=APPLICATION_RESULTS_URL,
    submit_name="j_idt138",
    date_from_name="txt-fromDate",
    date_to_name="txt-toDate",
    field_names=[
        "frm-advSearch",
        "txt-callSign",
        "txt-fileNum",
        "txt-frn",
        "txt-facilityID",
        "sel-state",
        "srch-city",
        "txt-applicantName",
        "txt-frequency",
        "txt-channel",
    ],
)
