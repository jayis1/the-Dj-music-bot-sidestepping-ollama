"""
utils/cookie_parser.py — Cookie parsing utilities for MBot.

Parses Set-Cookie HTTP headers into Netscape cookie file format,
and parses existing cookie files for yt-dlp authentication.

Used by the `?fetch_and_set_cookies` Discord command and the
Mission Control web dashboard to keep YouTube authenticated.
"""

import os
import re
import logging
from datetime import datetime


def parse_all_cookies(header: str) -> dict:
    """Parse a Set-Cookie HTTP header string into a {name: value} dict.

    A Set-Cookie header looks like:
        name=value; Domain=.example.com; Path=/; Secure; HttpOnly

    This extracts just the cookie name and value portion (before the first ';').
    Multiple cookies from multiple headers are merged into a single dict.

    Args:
        header: A raw Set-Cookie header value string.

    Returns:
        A dict mapping cookie names to their values.
    """
    if not header:
        return {}

    cookies = {}
    # The cookie name=value is always the first part, before any ';'
    # Strip leading/trailing whitespace
    cookie_part = header.split(";")[0].strip()

    if "=" in cookie_part:
        name, _, value = cookie_part.partition("=")
        cookies[name.strip()] = value.strip()
    # If there's no '=' it's just a flag cookie (rare but valid)
    elif cookie_part:
        cookies[cookie_part] = ""

    return cookies


def parse_set_cookie_to_netscape(header: str, domain: str = "") -> str:
    """Convert a Set-Cookie HTTP header line to Netscape cookie file format.

    Netscape cookie file format (tabs separated):
        domain  include_subdomains  path  secure  expiration  name  value

    Args:
        header: A raw Set-Cookie header value string.
        domain: Override domain if not found in the header.

    Returns:
        A single line in Netscape cookie file format.
    """
    if not header:
        return ""

    # Extract cookie name and value
    parts = header.split(";")
    name_value = parts[0].strip()
    name, _, value = name_value.partition("=")
    name = name.strip()
    value = value.strip()

    # Parse attributes from the remaining parts
    cookie_domain = domain
    path = "/"
    secure = False
    expires = 0

    for part in parts[1:]:
        part = part.strip()
        if not part:
            continue

        key_val = part.split("=", 1)
        key = key_val[0].strip().lower()

        if key == "domain":
            cookie_domain = key_val[1].strip() if len(key_val) > 1 else domain
            # Ensure domain starts with a dot for subdomain inclusion
            if cookie_domain and not cookie_domain.startswith("."):
                cookie_domain = "." + cookie_domain
        elif key == "path":
            path = key_val[1].strip() if len(key_val) > 1 else "/"
        elif key == "secure":
            secure = True
        elif key in ("expires", "max-age"):
            expires_str = key_val[1].strip() if len(key_val) > 1 else ""
            try:
                # Try to parse the expiration date
                # Common formats: "Thu, 01 Jan 2025 00:00:00 GMT"
                for fmt in (
                    "%a, %d %b %Y %H:%M:%S %Z",
                    "%a, %d %b %Y %H:%M:%S GMT",
                    "%A, %d-%b-%Y %H:%M:%S %Z",
                    "%d %b %Y %H:%M:%S %Z",
                ):
                    try:
                        dt = datetime.strptime(expires_str, fmt)
                        import time

                        expires = int(time.mktime(dt.timetuple()))
                        break
                    except ValueError:
                        continue
            except Exception:
                expires = 0

    include_subdomains = (
        "TRUE" if cookie_domain and cookie_domain.startswith(".") else "FALSE"
    )
    secure_str = "TRUE" if secure else "FALSE"

    if not cookie_domain:
        cookie_domain = domain

    return f"{cookie_domain}\t{include_subdomains}\t{path}\t{secure_str}\t{expires}\t{name}\t{value}"


def save_cookies_to_file(cookies: list, filepath: str = "") -> bool:
    """Save a list of Netscape-format cookie lines to a file.

    Args:
        cookies: List of Netscape-format cookie strings.
        filepath: Path to save the cookie file. Defaults to config value.

    Returns:
        True if successful, False otherwise.
    """
    if not filepath:
        filepath = getattr(
            __import__("config"), "YTDDL_COOKIEFILE", "youtube_cookie.txt"
        )

    try:
        with open(filepath, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# This is a generated file!  Do not edit.\n\n")
            for line in cookies:
                f.write(line + "\n")
        logging.info(f"Cookie parser: Saved {len(cookies)} cookies to {filepath}")
        return True
    except Exception as e:
        logging.error(f"Cookie parser: Failed to save cookies to {filepath}: {e}")
        return False


# ── Legacy log parsing (kept for backward compatibility) ──────────────


def parse_log_entry(log_line: str) -> dict | None:
    """Parse a single log line into a dictionary of its components.

    Assumes log format: YYYY-MM-DD HH:MM:SS,ms:LEVEL:NAME: MESSAGE
    """
    match = re.match(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}):([A-Z]+):([^:]+): (.*)",
        log_line,
    )
    if match:
        timestamp_str, level, name, message = match.groups()
        try:
            dt_object = datetime.strptime(
                timestamp_str.split(",")[0], "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            dt_object = None

        return {
            "timestamp": timestamp_str,
            "datetime": dt_object,
            "level": level,
            "name": name,
            "message": message.strip(),
        }
    return None


def parse_log_file(file_path: str) -> list:
    """Read a log file and parse each line into a list of dictionaries."""
    parsed_data = []
    if not os.path.exists(file_path):
        logging.warning(f"Log file not found at {file_path}")
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                entry = parse_log_entry(line)
                if entry:
                    parsed_data.append(entry)
    except Exception as e:
        logging.error(f"Error reading or parsing log file {file_path}: {e}")
    return parsed_data
