"""Backfill PremiumNumberContact.phone_is_valid."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from app.db import SessionLocal
from app.models import PremiumNumberContact
from app.premium_numbers.phone_normalization import canonicalize_phone
def run() -> None:
    db=SessionLocal()
    updated=0
    try:
        for contact in db.query(PremiumNumberContact).yield_per(500):
            is_valid=bool(canonicalize_phone(contact.normalized_phone_number) or canonicalize_phone(contact.display_phone_number))
            if contact.phone_is_valid != is_valid:
                contact.phone_is_valid=is_valid
                updated+=1
        db.commit()
        print(f"Backfilled phone_is_valid for {updated} contacts (owner scope: all owners in this DB)")
    finally: db.close()
if __name__ == "__main__": run()
