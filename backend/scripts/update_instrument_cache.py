from __future__ import annotations

import json

from app.core.database import SessionLocal, init_db
from app.services.instrument_cache_service import refresh_instrument_cache


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        result = refresh_instrument_cache(db)
        print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
