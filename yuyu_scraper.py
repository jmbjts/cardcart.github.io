"""
Scraper for yuyu-tei.jp card listing pages.
Extracts card number, name, and price from one or more page URLs.

Usage:
    python yuyu_scraper.py                         # scrapes default URLs
    python yuyu_scraper.py <url1> <url2> ...       # scrapes given URLs
    python yuyu_scraper.py --out cards.csv         # saves to CSV instead of printing
"""

import sys
import csv
import io
import time
import argparse
from typing import List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser

# Force UTF-8 on Windows so Japanese characters print correctly.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

DEFAULT_URLS = [
    "https://yuyu-tei.jp/sell/opc/s/promo-100",
    "https://yuyu-tei.jp/sell/opc/s/promo-200",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class CardParser(HTMLParser):
    """State-machine parser that extracts section label, card number, name, and price."""

    def __init__(self):
        super().__init__()
        self.cards: List[dict] = []
        self._current: dict = {}
        self._capture: Optional[str] = None
        self._current_section: str = ""
        self._in_section_h3: bool = False
        self._in_section_span: bool = False
        self._section_buf: str = ""
        # div depth tracking to stay inside col-12 mb-5 pb-5
        self._div_depth: int = 0
        self._main_div_depth: Optional[int] = None
        self._in_product_img: bool = False
        self._pending_image: str = ""

    @property
    def _in_main(self) -> bool:
        return self._main_div_depth is not None

    def _start_capture(self, field: str):
        self._capture = field
        self._current.setdefault(field, "")

    def _flush_card(self):
        if self._current.get("name") and self._current.get("price"):
            self.cards.append(dict(self._current))
            self._current = {}

    def handle_starttag(self, tag: str, attrs):
        attr = dict(attrs)
        cls = attr.get("class", "")

        if tag == "div":
            self._div_depth += 1
            if self._main_div_depth is None and cls == "col-12 mb-5 pb-5":
                self._main_div_depth = self._div_depth
            elif self._in_main and cls == "position-relative product-img":
                self._in_product_img = True
            return

        if not self._in_main:
            return

        if tag == "img" and self._in_product_img:
            self._pending_image = attr.get("src", "")
            self._in_product_img = False

        elif tag == "h3" and "text-primary" in cls and "fw-bold" in cls:
            self._in_section_h3 = True

        elif tag == "span":
            if self._in_section_h3 and "text-white" in cls and "fw-bold" in cls:
                self._in_section_span = True
                self._section_buf = ""
            elif "d-block border border-dark p-1 w-100 text-center my-2" in cls:
                self._flush_card()
                self._current = {"section": self._current_section, "image_url": self._pending_image}
                self._pending_image = ""
                self._start_capture("number")

        elif tag == "h4" and "text-primary fw-bold" in cls:
            self._start_capture("name")

        elif tag == "strong" and cls.strip() == "d-block text-end":
            self._start_capture("price")

    def handle_data(self, data: str):
        if not self._in_main:
            return
        if self._in_section_span:
            self._section_buf += data
        elif self._capture:
            self._current[self._capture] = self._current.get(self._capture, "") + data

    def handle_endtag(self, tag: str):
        if tag == "div":
            if (
                self._main_div_depth is not None
                and self._div_depth == self._main_div_depth
            ):
                self._flush_card()
                self._main_div_depth = None
            self._div_depth -= 1
            return

        if not self._in_main:
            return

        if tag == "h3":
            self._in_section_h3 = False

        elif tag == "span":
            if self._in_section_span:
                self._in_section_span = False
                self._current_section = self._section_buf.strip()
            elif self._capture == "number":
                self._current["number"] = self._current.get("number", "").strip()
                self._capture = None

        elif tag == "h4":
            if self._capture == "name":
                self._current["name"] = self._current.get("name", "").strip()
                self._capture = None

        elif tag == "strong":
            if self._capture == "price":
                self._current["price"] = self._current.get("price", "").strip()
                self._capture = None

    def close(self):
        super().close()
        self._flush_card()


def extract_series(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    if "-" in slug:
        parts = slug.split("-", 1)
        return parts[0].capitalize() + "-" + parts[1]
    return slug.upper()


def fetch_html(url: str) -> str:
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=15) as resp:
            charset = resp.headers.get_content_charset("utf-8")
            return resp.read().decode(charset)
    except HTTPError as e:
        print(f"  HTTP {e.code} for {url}", file=sys.stderr)
        return ""
    except URLError as e:
        print(f"  Failed to reach {url}: {e.reason}", file=sys.stderr)
        return ""


def scrape(url: str) -> List[dict]:
    print(f"Fetching {url} ...")
    html = fetch_html(url)
    if not html:
        return []

    parser = CardParser()
    parser.feed(html)
    parser.close()

    series = extract_series(url)
    for c in parser.cards:
        c["series"] = series

    print(f"  -> {len(parser.cards)} card(s) found")
    return parser.cards


def print_table(cards: List[dict]):
    if not cards:
        print("No cards found.")
        return

    col_w = [
        max(len("Section"),     max((len(c.get("section", "")) for c in cards), default=0)),
        max(len("Card Number"), max((len(c.get("number",  "")) for c in cards), default=0)),
        max(len("Card Name"),   max((len(c.get("name",    "")) for c in cards), default=0)),
        max(len("Price"),       max((len(c.get("price",   "")) for c in cards), default=0)),
    ]

    def row(a, b, c, d):
        return f"  {a:<{col_w[0]}}  {b:<{col_w[1]}}  {c:<{col_w[2]}}  {d:<{col_w[3]}}"

    sep = "  " + "  ".join("-" * w for w in col_w)
    print()
    print(row("Section", "Card Number", "Card Name", "Price"))
    print(sep)
    for c in cards:
        print(row(c.get("section", ""), c.get("number", ""), c.get("name", ""), c.get("price", "")))
    print()


def save_csv(cards: List[dict], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["series", "section", "number", "name", "price", "image_url"])
        writer.writeheader()
        writer.writerows(cards)
    print(f"Saved {len(cards)} row(s) to {path}")


def main():
    parser = argparse.ArgumentParser(description="Scrape card data from yuyu-tei.jp")
    parser.add_argument(
        "urls",
        nargs="*",
        default=DEFAULT_URLS,
        help="Page URLs to scrape (defaults to promo-100 and promo-200)",
    )
    parser.add_argument(
        "--out",
        metavar="FILE.csv",
        help="Save results to a CSV file instead of printing",
    )
    args = parser.parse_args()

    all_cards: list[dict] = []
    for i, url in enumerate(args.urls):
        all_cards.extend(scrape(url))
        if i < len(args.urls) - 1:
            time.sleep(1)  # polite delay between requests

    if args.out:
        save_csv(all_cards, args.out)
    else:
        print_table(all_cards)


if __name__ == "__main__":
    main()
