from __future__ import annotations

import sys

from audit_chatbot import ingest, query


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: audit_chatbot {ingest,query} ...")
        print()
        print("commands:")
        print("  ingest   Ingest archived audit docs")
        print("  query    Run station queries")
        print()
        print("web app:")
        print("  PYTHONPATH=src uvicorn audit_chatbot.app:app --reload --port 8790")
        return 0

    command, rest = argv[0], argv[1:]
    if command == "ingest":
        return ingest.main(rest)
    if command == "query":
        return query.main(rest)
    print(f"audit_chatbot: error: argument command: invalid choice: '{command}' (choose from ingest, query)")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
