#!/usr/bin/env python3
"""Exercise GET /api/data/health against a mongomock DB populated by a real
nflverse ingestion run (2024 season only; downloads are cached).

Prints the health payload as JSON. This is a verification script, not part
of the shipped product.
"""

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock

from app.data_providers import NflverseProvider
from app.database import ensure_indexes
from app.ingestion import run_ingestion


def main() -> int:
    db = mongomock.MongoClient()["money_by_numbers"]
    ensure_indexes(db)
    run_doc = run_ingestion(db, NflverseProvider(), [2024])
    print(f"ingestion: {run_doc['status']} "
          f"games={run_doc['records_accepted']['games']} "
          f"stats={run_doc['records_accepted']['game_stats']} "
          f"errors={len(run_doc['errors'])}", file=sys.stderr)

    import app.routers.data_health as dh

    class FakeClient:
        def __getitem__(self, name):
            assert name == dh.get_database_name()
            return db

        def close(self):
            pass

    with patch.object(dh, "get_settings",
                       return_value=SimpleNamespace(MONGODB_URI="mongodb://fake")), \
         patch.object(dh, "get_mongo_client", return_value=FakeClient()):
        payload = dh.data_health()
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
