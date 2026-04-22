#!/usr/bin/env python3
"""
update.py
---------
Fetch the latest BOM forecast XML and generate the ICS file.
Designed to be run by GitHub Actions on a schedule.

The output ICS is written to the /docs folder, which is served
by GitHub Pages at your custom domain.
"""

import ftplib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bom_forecast_to_ics import parse_bom_xml, build_ics

BOM_HOST    = "ftp.bom.gov.au"
BOM_PATH    = "anon/gen/fwo"
XML_FILE    = "IDW12300.xml"
MAX_RETRIES = 2
OUTPUT_DIR  = Path(__file__).parent / "docs"


def download(dest: Path) -> bool:
    attempts = 1 + MAX_RETRIES
    for attempt in range(1, attempts + 1):
        try:
            print(f"Connecting to BOM FTP (attempt {attempt}/{attempts}) …")
            with ftplib.FTP(BOM_HOST, timeout=30) as ftp:
                ftp.login(user="anonymous", passwd="")
                ftp.set_pasv(True)
                with open(dest, "wb") as f:
                    ftp.retrbinary(f"RETR {BOM_PATH}/{XML_FILE}", f.write)
            print(f"Downloaded {dest.stat().st_size} bytes.")
            return True
        except Exception as exc:
            print(f"Attempt {attempt}/{attempts} failed: {exc}")
            if attempt < attempts:
                time.sleep(15 * attempt)
    return False


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    xml_path = OUTPUT_DIR / XML_FILE
    ics_path = OUTPUT_DIR / (Path(XML_FILE).stem + ".ics")

    if not download(xml_path):
        print("All download attempts failed.")
        # Exit 0 so GitHub Actions doesn't fail the run —
        # the existing ICS in the repo remains published.
        sys.exit(0)

    days, location_name = parse_bom_xml(xml_path)
    print(f"Parsed {len(days)} day(s) for '{location_name}'.")

    ics_content = build_ics(days, Path(XML_FILE).stem.upper(), location_name)
    ics_path.write_text(ics_content, encoding="utf-8")
    print(f"ICS written → {ics_path}")


if __name__ == "__main__":
    main()
