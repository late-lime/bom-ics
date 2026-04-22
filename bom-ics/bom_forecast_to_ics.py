#!/usr/bin/env python3
"""
bom_forecast_to_ics.py
----------------------
Convert a Bureau of Meteorology city forecast XML file (e.g. IDW12300.xml)
into an ICS calendar file with one all-day event per forecast day.

Usage:
    python bom_forecast_to_ics.py <input.xml> [output.ics]

If output path is omitted, the ICS file is written alongside the input XML
with the same base name (e.g. IDW12300.ics).

The script targets the two <area> elements that contain per-day data:
  - type="metropolitan"  — full forecast text (used for DESCRIPTION)
  - type="location"      — precis, max/min temps, rain chance, rainfall range

It picks the first "location" child of the metropolitan area (the city centre).
"""

import sys
import re
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Emoji mapping
# ---------------------------------------------------------------------------

def pick_emoji(precis: str, metro_text: str) -> str:
    """
    Choose a weather emoji based on the short precis and/or the longer
    metropolitan forecast text.  Priority: storm > rain > shower > cloudy.
    """
    combined = (precis + " " + metro_text).lower()

    has_storm      = "thunderstorm" in combined
    has_rain       = "rain" in combined and "no rain" not in combined
    has_showers    = "shower" in combined
    has_cloudy     = "cloudy" in combined
    has_partly     = "partly" in combined
    has_sunny      = "sunny" in combined or "clear" in combined

    # Work from most-severe downward
    if has_storm and (has_rain or has_showers):
        return "⛈️"   # thunderstorm + precipitation
    if has_storm:
        return "🌩️"   # dry storm
    if has_rain:
        return "🌧️"   # rain (not just showers)
    if has_showers and has_cloudy and not has_partly:
        return "🌧️"   # heavy showers / cloudy
    if has_showers:
        return "🌦️"   # shower possible
    if has_cloudy and not has_partly:
        return "☁️"   # fully overcast
    if has_partly:
        return "⛅"   # partly cloudy
    if has_sunny:
        return "☀️"   # sunny / clear
    return "🌡️"        # fallback


# ---------------------------------------------------------------------------
# ICS helpers
# ---------------------------------------------------------------------------

def ics_escape(text: str) -> str:
    """Escape special characters for ICS text fields."""
    text = text.replace("\\", "\\\\")
    text = text.replace(";",  "\\;")
    text = text.replace(",",  "\\,")
    text = text.replace("\n", "\\n")
    return text


def fold_line(line: str) -> str:
    """
    RFC 5545 §3.1 — lines must not exceed 75 octets; continuation lines
    begin with a single space.
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks = []
    while len(encoded) > 75:
        # Slice at 75 bytes, but don't split a multi-byte char
        cut = 75 if not chunks else 74  # continuation lines get 1 space prefix
        chunk = encoded[:cut].decode("utf-8", errors="ignore")
        # Walk back if the cut is inside a multi-byte sequence
        while len(chunk.encode("utf-8")) > cut:
            chunk = chunk[:-1]
        chunks.append(chunk)
        encoded = encoded[len(chunk.encode("utf-8")):]
    chunks.append(encoded.decode("utf-8"))
    return ("\r\n ").join(chunks)


def make_vevent(date_str: str, summary: str, description: str,
                location: str, uid_prefix: str) -> str:
    """Return a VEVENT block as a string."""
    # date_str like "2026-04-22"
    date_compact = date_str.replace("-", "")
    next_date    = date_compact[:4] + date_compact[4:6] + str(int(date_compact[6:]) + 1).zfill(2)
    # Crude next-day calc — works for all days except month-end edge cases;
    # for a production tool use datetime arithmetic instead.
    from datetime import date, timedelta
    d      = date.fromisoformat(date_str)
    d_next = d + timedelta(days=1)
    dtstart = d.strftime("%Y%m%d")
    dtend   = d_next.strftime("%Y%m%d")

    uid   = f"{uid_prefix}-{dtstart}@bom.gov.au"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VEVENT",
        f"DTSTART;VALUE=DATE:{dtstart}",
        f"DTEND;VALUE=DATE:{dtend}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DESCRIPTION:{ics_escape(description)}",
        f"LOCATION:{ics_escape(location)}",
        f"URL:https://www.bom.gov.au",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        "END:VEVENT",
    ]
    return "\r\n".join(fold_line(l) for l in lines)


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def get_text(period, text_type: str) -> str:
    """Return stripped text content of a <text type="..."> child, or ''."""
    for el in period.findall("text"):
        if el.get("type") == text_type:
            # Collect all inner text (handles mixed content / sub-elements)
            return "".join(el.itertext()).strip()
    return ""


def get_element(period, elem_type: str) -> str:
    """Return stripped text of an <element type="..."> child, or ''."""
    for el in period.findall("element"):
        if el.get("type") == elem_type:
            return (el.text or "").strip()
    return ""


def parse_bom_xml(xml_path: Path):
    """
    Parse a BOM forecast XML file and return a list of day dicts:
        date, max_temp, min_temp, precis, rain_chance,
        rainfall_range, metro_text
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    forecast_el = root.find("forecast")
    if forecast_el is None:
        raise ValueError("No <forecast> element found in XML.")

    # --- Collect metropolitan forecast texts keyed by date string ----------
    metro_texts = {}   # "2026-04-22" -> full forecast text
    metro_area  = None
    for area in forecast_el.findall("area"):
        if area.get("type") == "metropolitan":
            metro_area = area
            for period in area.findall("forecast-period"):
                start = period.get("start-time-local", "")
                date_key = start[:10]           # "YYYY-MM-DD"
                text = get_text(period, "forecast")
                if date_key and text:
                    metro_texts[date_key] = text
            break   # take only the first metropolitan area

    if metro_area is None:
        raise ValueError("No metropolitan area found in XML.")

    # --- Find the first location child of the metropolitan area ------------
    metro_aac   = metro_area.get("aac")
    location_el = None
    for area in forecast_el.findall("area"):
        if area.get("type") == "location" and area.get("parent-aac") == metro_aac:
            location_el = area
            break

    if location_el is None:
        raise ValueError("No location child of metropolitan area found in XML.")

    location_name = location_el.get("description", "City Centre")

    # --- Extract per-period data from the location element -----------------
    days = []
    for period in location_el.findall("forecast-period"):
        start    = period.get("start-time-local", "")
        date_key = start[:10]
        if not date_key:
            continue

        max_temp      = get_element(period, "air_temperature_maximum")
        min_temp      = get_element(period, "air_temperature_minimum")
        precis        = get_text(period,    "precis").rstrip(".")
        rain_chance   = get_text(period,    "probability_of_precipitation")
        rainfall_range= get_element(period, "precipitation_range")
        metro_text    = metro_texts.get(date_key, "")

        days.append({
            "date":          date_key,
            "max_temp":      max_temp,
            "min_temp":      min_temp,
            "precis":        precis,
            "rain_chance":   rain_chance,
            "rainfall_range": rainfall_range,
            "metro_text":    metro_text,
            "location_name": location_name,
        })

    return days, location_name


# ---------------------------------------------------------------------------
# Build the ICS
# ---------------------------------------------------------------------------

def build_ics(days: list, product_id: str, location_name: str) -> str:
    """Assemble the full ICS calendar string."""

    header = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//BOM {product_id}//{location_name} Forecast//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{location_name} Weather Forecast",
        "X-WR-TIMEZONE:Australia/Perth",
    ])
    footer = "END:VCALENDAR"

    events = []
    for day in days:
        date          = day["date"]
        max_temp      = day["max_temp"]
        min_temp      = day["min_temp"]
        precis        = day["precis"]
        rain_chance   = day["rain_chance"]
        rainfall_range= day["rainfall_range"]
        metro_text    = day["metro_text"]
        loc_name      = day["location_name"]

        emoji = pick_emoji(precis, metro_text)

        # --- Build SUMMARY ---
        temp_part  = f"{max_temp}°C" if max_temp else ""
        rain_part  = f"({rainfall_range})" if rainfall_range else ""
        summary_parts = [p for p in [emoji, temp_part, "—", precis, rain_part] if p and p != "—"]
        # Re-insert the dash correctly
        if temp_part and precis:
            summary = f"{emoji} {temp_part} — {precis}"
            if rain_part:
                summary += f" {rain_part}"
        elif temp_part:
            summary = f"{emoji} {temp_part}"
        else:
            summary = f"{emoji} {precis}"

        # --- Build DESCRIPTION ---
        desc_lines = []
        if metro_text:
            desc_lines.append(metro_text)
        temps = []
        if min_temp:
            temps.append(f"Min: {min_temp}°C")
        if max_temp:
            temps.append(f"Max: {max_temp}°C")
        if temps:
            desc_lines.append("  ".join(temps))
        if rain_chance:
            desc_lines.append(f"Chance of rain: {rain_chance}")
        if rainfall_range:
            desc_lines.append(f"Rainfall: {rainfall_range}")
        description = "\n".join(desc_lines)

        events.append(make_vevent(
            date_str    = date,
            summary     = summary,
            description = description,
            location    = f"{loc_name}, WA",
            uid_prefix  = product_id,
        ))

    return header + "\r\n" + "\r\n".join(events) + "\r\n" + footer + "\r\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python bom_forecast_to_ics.py <input.xml> [output.ics]")
        sys.exit(1)

    xml_path = Path(sys.argv[1])
    if not xml_path.exists():
        print(f"Error: file not found: {xml_path}")
        sys.exit(1)

    ics_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else xml_path.with_suffix(".ics")

    # Use the XML filename stem as the product ID (e.g. "IDW12300")
    product_id = xml_path.stem.upper()

    print(f"Parsing {xml_path} …")
    days, location_name = parse_bom_xml(xml_path)
    print(f"  Found {len(days)} forecast day(s) for '{location_name}'.")

    ics_content = build_ics(days, product_id, location_name)

    ics_path.write_text(ics_content, encoding="utf-8")
    print(f"  Written → {ics_path}")


if __name__ == "__main__":
    main()
