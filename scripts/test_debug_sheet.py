"""
Local test script for the Store Debug sheet.

Runs analyse_stores() against a TEST spreadsheet so you can verify the
debug sheet output without touching production data.

Setup:
  1. Create a copy of your Store Sheet in Google Sheets
  2. Make sure it has a tab named "Store Debug" (can be empty)
  3. Set TEST_STORE_SPREADSHEET_ID below to your test sheet ID
  4. Run: python scripts/test_debug_sheet.py

The script temporarily overrides STORE_SPREADSHEET_ID so all writes
go to the test sheet. Production data is never touched.

To get your test sheet ID:
  Open the sheet in a browser — the ID is the long string in the URL:
  https://docs.google.com/spreadsheets/d/<THIS_PART>/edit
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Point at your test spreadsheet ───────────────────────────────────────────
# Replace with your test sheet ID before running
TEST_STORE_SPREADSHEET_ID = "1cKiZqVu88_umUbrGPXk-dmZhHEzx1uL-pGR6dQOJyaU"

if TEST_STORE_SPREADSHEET_ID == "YOUR_TEST_SHEET_ID_HERE":
    print("⚠ Set TEST_STORE_SPREADSHEET_ID in this script before running.")
    sys.exit(1)

# Override the env var before importing stores so all writes go to the test sheet
os.environ["STORE_SPREADSHEET_ID_OVERRIDE"] = TEST_STORE_SPREADSHEET_ID

# ─────────────────────────────────────────────────────────────────────────────

from dotenv import load_dotenv
load_dotenv()

import stores
import constants

# Patch STORE_SPREADSHEET_ID at runtime to redirect all sheet writes
constants.BOT_DATABASE_SPREADSHEET_ID = TEST_STORE_SPREADSHEET_ID
stores.BOT_DATABASE_SPREADSHEET_ID    = TEST_STORE_SPREADSHEET_ID  # stores.py reads it at call time via _gs

# Also patch the range names to use the test sheet's tab names if needed
# (leave as-is if your test sheet has the same tab names)

# Optional: override reference_date to simulate a specific day
# e.g. to test what the sheet looks like as of last Sunday:
# from datetime import timedelta
# REFERENCE_DATE = date.today() - timedelta(days=2)
REFERENCE_DATE = None  # None = use today


def main():
    print(f"\n{'='*60}")
    print(f"  Store Debug Sheet — Local Test")
    print(f"  Writing to: {TEST_STORE_SPREADSHEET_ID}")
    print(f"  Reference date: {REFERENCE_DATE or 'today (' + str(date.today()) + ')'}")
    print(f"{'='*60}\n")

    analysis = stores.analyse_stores(reference_date=REFERENCE_DATE)

    print(f"\n✓ Done.")
    print(f"  Regular:      {len(analysis['regular'])} store(s)")
    print(f"  Semi-Regular: {len(analysis['semi_regular'])} store(s)")
    print(f"\nOpen your test sheet and check the 'Store Debug' tab.")
    print(f"https://docs.google.com/spreadsheets/d/{TEST_STORE_SPREADSHEET_ID}/edit")


if __name__ == "__main__":
    main()