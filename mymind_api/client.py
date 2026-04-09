"""
mymind — unofficial Python API client.

`mymind login` opens a browser → you sign in with Google/Apple →
tokens are captured and stored in your system keychain (macOS Keychain,
Windows Credential Locker, etc). No passwords ever touch this code.
Tokens auto-refresh when they expire.

Usage:
    from mymind_api import MyMind
    mind = MyMind()
    cards = mind.get_all_cards()
    mind.create_note("# Hello", title="My Note", tags=["idea"])
    mind.save_url("https://example.com")
"""

import json
import re
import logging
import requests
import msgpack
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field

log = logging.getLogger("mymind")

BASE_URL = "https://access.mymind.com"
CONFIG_DIR = Path.home() / ".mymind"
KEYRING_SERVICE = "mymind-api"


# ── Keychain helpers ─────────────────────────────────────


def _store_tokens(jwt: str, cid: str, authenticity_token: str):
    """Save tokens to system keychain."""
    import keyring
    keyring.set_password(KEYRING_SERVICE, "jwt", jwt)
    keyring.set_password(KEYRING_SERVICE, "cid", cid)
    keyring.set_password(KEYRING_SERVICE, "authenticity_token", authenticity_token)


def _load_tokens() -> Optional[dict]:
    """Load tokens from system keychain. Returns None if not found."""
    import keyring
    jwt = keyring.get_password(KEYRING_SERVICE, "jwt")
    cid = keyring.get_password(KEYRING_SERVICE, "cid")
    token = keyring.get_password(KEYRING_SERVICE, "authenticity_token")
    if jwt and cid and token:
        return {"jwt": jwt, "cid": cid, "authenticity_token": token}
    return None


def _clear_tokens():
    """Remove tokens from keychain."""
    import keyring
    for key in ("jwt", "cid", "authenticity_token"):
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass


# ── Data ─────────────────────────────────────────────────


@dataclass
class Card:
    slug: str
    title: str
    description: str
    domain: str
    source_url: str
    tags: List[str]
    created: str
    modified: str
    card_type: str = ""
    prose_markdown: str = ""
    note_markdown: str = ""
    raw: dict = field(default_factory=dict, repr=False)


# ── Browser login ────────────────────────────────────────


def _read_multiline(prompt: str = "> ") -> str:
    """Read multi-line paste from terminal. Stops on 2s of no input."""
    import sys, select

    print(prompt, end="", flush=True)
    lines = []
    try:
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 2.0)
            if ready:
                line = sys.stdin.readline()
                if not line:
                    break
                lines.append(line)
            else:
                # 2 seconds of silence = done pasting
                if lines:
                    break
    except (EOFError, KeyboardInterrupt):
        pass
    return "\n".join(lines)


def _parse_tokens(text: str) -> dict:
    """Extract jwt, cid, and authenticity_token from cURL or raw headers."""
    jwt = cid = authenticity_token = ""

    jwt_match = re.search(r'_jwt=([^\s;\'\"]+)', text)
    if jwt_match:
        jwt = jwt_match.group(1)

    cid_match = re.search(r'_cid=([^\s;\'\"]+)', text)
    if cid_match:
        cid = cid_match.group(1)

    # Raw headers: "x-authenticity-token\n<value>" (DevTools copy)
    token_match = re.search(r'x-authenticity-token\s*\n\s*([^\s\n]+)', text, re.IGNORECASE)
    if not token_match:
        # cURL -H format: "x-authenticity-token: <value>"
        token_match = re.search(r'x-authenticity-token[:\s]+([^\s\'\"]+)', text, re.IGNORECASE)
    if token_match:
        authenticity_token = token_match.group(1)

    if not jwt or not cid or not authenticity_token:
        missing = []
        if not jwt:
            missing.append("_jwt")
        if not cid:
            missing.append("_cid")
        if not authenticity_token:
            missing.append("x-authenticity-token")
        raise RuntimeError(
            f"Could not find: {', '.join(missing)}. "
            "Make sure you copied the full request headers for the 'cards' request."
        )

    return {"jwt": jwt, "cid": cid, "authenticity_token": authenticity_token}


def browser_login() -> dict:
    """Open default browser to mymind, grab tokens from Network tab.

    1. Opens mymind in your browser (Dia, Safari, Chrome, whatever)
    2. You sign in with passkeys, Google, Apple
    3. Open Network tab, refresh, click 'cards' request
    4. Copy the request headers and paste here

    Returns:
        Dict with jwt, cid, authenticity_token.
    """
    import webbrowser

    webbrowser.open("https://access.mymind.com/signin")
    print()
    print("Sign in to mymind in your browser.")
    print()
    print("After you see your cards:")
    print("  1. Open DevTools (Cmd+Option+I) → Network tab")
    print("  2. Type 'cards' in the Network filter bar")
    print("  3. Refresh the page (Cmd+R) — a 'cards' request appears")
    print("  4. Right-click it → Copy as cURL  (or copy the request headers)")
    print("  5. Paste here and wait 2 seconds:")
    print()

    text = _read_multiline()
    return _parse_tokens(text)


# ── Client ───────────────────────────────────────────────


class MyMind:
    """mymind API client with automatic token management.

    Tokens are loaded from the system keychain and auto-refreshed on expiry.
    Run `mymind login` first to authenticate.
    """

    def __init__(self):
        tokens = _load_tokens()
        if not tokens:
            raise ValueError(
                "Not logged in. Run: mymind login"
            )
        self._jwt = tokens["jwt"]
        self._cid = tokens["cid"]
        self._authenticity_token = tokens["authenticity_token"]

    def _refresh_tokens(self):
        """Re-login via browser and store fresh tokens."""
        log.info("Session expired, re-authenticating...")
        tokens = browser_login()
        self._jwt = tokens["jwt"]
        self._cid = tokens["cid"]
        self._authenticity_token = tokens["authenticity_token"]
        _store_tokens(**tokens)
        log.info("Tokens refreshed.")

    def _headers(self) -> dict:
        return {
            "x-authenticity-token": self._authenticity_token,
            "cookie": f"_cid={self._cid}; _jwt={self._jwt}",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }

    def _headers_json(self) -> dict:
        h = self._headers()
        h["Content-Type"] = "application/json"
        h["accept"] = "application/json"
        return h

    def _headers_msgpack(self) -> dict:
        h = self._headers()
        h["accept"] = "application/msgpack"
        return h

    def _request(self, method: str, path: str, retry: bool = True, **kwargs) -> requests.Response:
        """Make an authenticated request. Auto-refreshes tokens on auth failure."""
        url = f"{BASE_URL}{path}"
        headers = kwargs.pop("headers", None) or self._headers()
        resp = requests.request(
            method, url, headers=headers, allow_redirects=False, **kwargs
        )

        if resp.status_code in (302, 401, 403) and retry:
            log.info("Got %d, refreshing tokens...", resp.status_code)
            self._refresh_tokens()
            if "Content-Type" in headers:
                new_headers = self._headers_json()
            else:
                new_headers = self._headers()
            return self._request(method, path, retry=False, headers=new_headers, **kwargs)

        if resp.status_code in (302, 401, 403):
            raise PermissionError(
                "Auth failed even after token refresh. Run: mymind login"
            )
        resp.raise_for_status()
        return resp

    # ── Read ─────────────────────────────────────────────

    def get_all_cards(self) -> List[Card]:
        """Fetch all cards, sorted newest-first."""
        resp = self._request("GET", "/cards", headers=self._headers_msgpack())
        unpacker = msgpack.Unpacker(raw=False)
        unpacker.feed(resp.content)
        cards = []
        for item in unpacker:
            raw = json.loads(item["json"]) if isinstance(item.get("json"), str) else item.get("json", {})
            # Extract slug from html data-id or from raw
            slug = raw.get("id", "")
            if not slug and isinstance(item.get("html"), str):
                m = re.search(r'data-id="([^"]+)"', item["html"])
                if m:
                    slug = m.group(1)
            cards.append(_parse_card(slug, raw))
        return cards

    def search(self, query: str) -> dict:
        """Server-side full-text search."""
        resp = self._request("GET", f"/search?q={query}", headers=self._headers_json())
        return resp.json()

    def filter_cards(
        self,
        tags: Optional[List[str]] = None,
        domain: Optional[str] = None,
        card_type: Optional[str] = None,
        text: Optional[str] = None,
        limit: int = 50,
    ) -> List[Card]:
        """Filter cards client-side by tags, domain, type, and/or text content.

        All filters are AND-ed. Multiple tags are also AND-ed (card must have all).
        Text matches against title, description, and prose.

        Args:
            tags: Filter by tag names (case-insensitive). Card must have ALL listed tags.
            domain: Filter by source domain (e.g. "x.com", "twitter.com").
            card_type: Filter by card type. Auto-assigned types include:
                WebPage, Image, XPost, Article, YouTubeVideo, InstagramReel,
                Video, Note, Snippet (alias for Content — clipped text from pages),
                Quotation, RedditPost, Product, Post, Recipe,
                MusicRecording, SoftwareApplication, Book, Movie, Document.
            text: Filter by text content in title/description/prose.
            limit: Max results to return.
        """
        cards = self.get_all_cards()
        results = []
        tags_lower = [t.lower() for t in tags] if tags else None
        text_lower = text.lower() if text else None
        domain_lower = domain.lower() if domain else None

        # Normalize type aliases (mymind UI name -> API card_type)
        type_aliases = {"snippet": "content", "snippets": "content"}
        type_lower = card_type.lower() if card_type else None
        if type_lower:
            type_lower = type_aliases.get(type_lower, type_lower)

        for c in cards:
            if tags_lower:
                card_tags_lower = [t.lower() for t in c.tags]
                if not all(tl in card_tags_lower for tl in tags_lower):
                    continue
            if domain_lower and domain_lower not in c.domain.lower() and domain_lower not in c.source_url.lower():
                continue
            if type_lower and c.card_type.lower() != type_lower:
                continue
            if text_lower:
                haystack = f"{c.title} {c.description} {c.prose_markdown}".lower()
                if text_lower not in haystack:
                    continue
            results.append(c)
            if len(results) >= limit:
                break
        return results

    def get_object(self, card_id: str) -> dict:
        """Get full object metadata (id, title, tags, spaces, notes, timestamps)."""
        resp = self._request("GET", f"/objects/{card_id}", headers=self._headers_json())
        return resp.json()

    def get_card_content(self, card_id: str) -> dict:
        """Get full card content (title, description, prose, source, tags)."""
        resp = self._request("GET", f"/cards/{card_id}", headers=self._headers_json())
        return resp.json()

    def get_card_image(self, card_id: str, max_width: int = 1024) -> Optional[bytes]:
        """Get a card's image as bytes (authenticated). Returns None if no image.

        mymind image URLs are auth-protected — they can't be used as external
        embeds. This method fetches the bytes server-side with auth headers,
        which can then be returned as base64 ImageContent to the LLM.

        Args:
            card_id: The card's ID/slug.
            max_width: Max image width in pixels (height scales proportionally). Default 1024.
        """
        content = self.get_card_content(card_id)
        obj = content.get("object")
        if not obj or not obj.get("path"):
            return None

        path = obj["path"]
        orig_w = obj.get("width", max_width)
        orig_h = obj.get("height", max_width)

        if orig_w > max_width:
            h = int(orig_h * (max_width / orig_w))
            w = max_width
        else:
            w, h = orig_w, orig_h

        url = f"{BASE_URL}/media/{path};{w}x{h}.webp"
        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code == 200:
            return resp.content
        return None

    def get_object_tags(self, card_id: str) -> list:
        """Get tags for a specific card."""
        resp = self._request("GET", f"/objects/{card_id}/tags", headers=self._headers_json())
        return resp.json()

    # ── Tags (global) ────────────────────────────────────

    def get_tags(self) -> list:
        """Get all tags sorted by usage count."""
        resp = self._request("GET", "/tags", headers=self._headers_json())
        return resp.json()

    def get_custom_tags(self) -> list:
        """Get only user-created (custom) tags, excluding AI-generated ones.

        Custom tags are the ones you manually created — these tend to be
        more intentional and useful for organizing than the AI-generated tags.
        """
        all_tags = self.get_tags()
        return [t for t in all_tags if t.get("flags") == 8]

    # ── Create ───────────────────────────────────────────

    def create_note(self, content: str, title: str = "", tags: Optional[List[str]] = None) -> dict:
        """Create a note with markdown content."""
        payload = {
            "title": title,
            "prose": {
                "type": "doc",
                "content": _markdown_to_prose(content),
            },
            "type": "Note",
        }
        resp = self._request("POST", "/objects", headers=self._headers_json(), json=payload)
        result = resp.json()

        if tags:
            card_id = result.get("id", "")
            for tag in tags:
                self.add_tag(card_id, tag)
        return result

    def save_url(self, url: str, tags: Optional[List[str]] = None) -> dict:
        """Save a URL/bookmark."""
        payload = {"url": url, "type": "WebPage"}
        resp = self._request("POST", "/objects", headers=self._headers_json(), json=payload)
        result = resp.json()

        if tags:
            card_id = result.get("id", "")
            for tag in tags:
                self.add_tag(card_id, tag)
        return result

    # ── Update ───────────────────────────────────────────

    def update_object(self, card_id: str, updates: dict) -> dict:
        """Update a card's properties (e.g. title)."""
        resp = self._request(
            "PATCH", f"/objects/{card_id}",
            headers=self._headers_json(),
            json=updates,
        )
        return resp.json()

    def add_tag(self, card_id: str, tag_name: str) -> None:
        """Add a tag to a card."""
        self._request(
            "POST", f"/objects/{card_id}/tags",
            headers=self._headers_json(),
            json={"name": tag_name},
        )

    def remove_tag(self, card_id: str, tag_name: str) -> None:
        """Remove a tag from a card."""
        self._request(
            "DELETE", f"/objects/{card_id}/tags",
            headers=self._headers_json(),
            json={"name": tag_name},
        )

    # ── Delete ───────────────────────────────────────────

    def delete_card(self, card_id: str) -> None:
        """Delete a card by ID."""
        self._request("DELETE", f"/objects/{card_id}")

    # ── Spaces ───────────────────────────────────────────

    def get_spaces(self) -> list:
        """Get all spaces."""
        resp = self._request("GET", "/spaces", headers=self._headers_json())
        spaces = resp.json()
        return [
            {
                "id": s["id"],
                "name": s["name"],
                "color": s.get("color", ""),
                "query": s.get("query"),
                "card_count": len(s.get("objects", [])),
            }
            for s in spaces
        ]

    def get_space_cards(self, space_id: str) -> List[dict]:
        """Get all cards in a specific space.

        Args:
            space_id: The space's ID.
        """
        resp = self._request("GET", f"/spaces/{space_id}", headers=self._headers_json())
        space = resp.json()
        card_ids = [obj["id"] for obj in space.get("objects", [])]
        if not card_ids:
            return []

        # Hydrate with full card data
        all_cards = self.get_all_cards()
        card_map = {c.slug: c for c in all_cards}
        results = []
        for cid in card_ids:
            c = card_map.get(cid)
            if c:
                results.append({
                    "id": c.slug,
                    "title": c.title,
                    "type": c.card_type,
                    "description": c.description,
                    "tags": c.tags,
                    "source_url": c.source_url,
                    "created": c.created,
                    "modified": c.modified,
                })
        return results

    def create_space(self, name: str, color: str = "#fdf06f") -> dict:
        """Create a manual space."""
        resp = self._request(
            "POST", "/spaces",
            headers=self._headers_json(),
            json={"name": name, "color": color},
        )
        return resp.json()

    def create_smart_space(self, name: str, filters: List[str], color: str = "#fdf06f") -> dict:
        """Create a smart space with auto-populating query filters."""
        resp = self._request(
            "POST", "/spaces",
            headers=self._headers_json(),
            json={
                "name": name,
                "color": color,
                "query": {"filters": filters},
            },
        )
        return resp.json()

    def delete_space(self, space_id: str) -> None:
        """Delete a space."""
        self._request("DELETE", f"/spaces/{space_id}")

    # ── Utilities ────────────────────────────────────────

    def test_connection(self) -> bool:
        """Test if connection works (refreshes tokens if needed)."""
        try:
            self._request("GET", "/cards", headers=self._headers_msgpack())
            return True
        except Exception:
            return False


# ── Helpers ──────────────────────────────────────────────


def _parse_card(slug: str, raw: dict) -> Card:
    tags = [t["name"] for t in raw.get("tags", []) if "name" in t]
    source = raw.get("source", {})

    prose_md = ""
    if raw.get("prose", {}).get("content"):
        prose_md = _prose_to_markdown(raw["prose"]["content"])

    note_md = ""
    note = raw.get("note")
    if note and note.get("prose", {}).get("content"):
        note_md = _prose_to_markdown(note["prose"]["content"])

    return Card(
        slug=slug,
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        domain=raw.get("domain", ""),
        source_url=source.get("url", ""),
        tags=tags,
        created=raw.get("created", ""),
        modified=raw.get("modified", ""),
        card_type=raw.get("type", ""),
        prose_markdown=prose_md,
        note_markdown=note_md,
        raw=raw,
    )


def _prose_to_markdown(content: list) -> str:
    parts = []
    for node in content:
        if not node:
            continue
        t = node.get("type", "")

        if t == "heading":
            level = node.get("attrs", {}).get("level", 1)
            text = "".join(c.get("text", "") for c in node.get("content", []))
            parts.append(f"{'#' * level} {text}\n")
        elif t == "paragraph":
            if not node.get("content"):
                parts.append("\n")
            else:
                text = _inline_to_markdown(node["content"])
                parts.append(f"{text}\n")
        elif t == "orderedList":
            idx = node.get("attrs", {}).get("start", 1)
            for item in node.get("content", []):
                item_text = ""
                for c in item.get("content", []):
                    if c.get("type") == "paragraph":
                        item_text += "".join(x.get("text", "") for x in c.get("content", []))
                parts.append(f"{idx}. {item_text}\n")
                idx += 1
        elif t == "taskList":
            for item in node.get("content", []):
                checked = item.get("attrs", {}).get("checked", False)
                mark = "x" if checked else " "
                item_text = ""
                for c in item.get("content", []):
                    if c.get("type") == "paragraph":
                        item_text += "".join(x.get("text", "") for x in c.get("content", []))
                parts.append(f"- [{mark}] {item_text}\n")
        elif t == "codeBlock":
            lang = node.get("attrs", {}).get("language", "")
            code = "".join(c.get("text", "") for c in node.get("content", []))
            parts.append(f"```{lang}\n{code}\n```\n")
        elif t == "horizontalRule":
            parts.append("---\n")

    return "\n".join(parts)


def _inline_to_markdown(content: list) -> str:
    parts = []
    for c in content:
        if not c:
            continue
        text = c.get("text", "")
        for mark in c.get("marks", []):
            mt = mark.get("type", "")
            if mt == "bold":
                text = f"**{text}**"
            elif mt == "italic":
                text = f"*{text}*"
            elif mt == "strike":
                text = f"~~{text}~~"
            elif mt == "code":
                text = f"`{text}`"
            elif mt == "highlight":
                text = f"=={text}=="
        parts.append(text)
    return "".join(parts)


def _markdown_to_prose(markdown: str) -> list:
    lines = markdown.split("\n")
    content = []
    in_code = False
    code_buf = ""
    code_lang = ""

    for line in lines:
        if line.startswith("```"):
            if not in_code:
                in_code = True
                code_lang = line[3:].strip()
                code_buf = ""
            else:
                content.append({
                    "type": "codeBlock",
                    "attrs": {"language": code_lang},
                    "content": [{"type": "text", "text": code_buf.rstrip("\n")}],
                })
                in_code = False
            continue
        if in_code:
            code_buf += line + "\n"
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading:
            content.append({
                "type": "heading",
                "attrs": {"level": len(heading.group(1))},
                "content": [{"type": "text", "text": heading.group(2)}],
            })
            continue

        task = re.match(r"^- \[(x| )\] (.+)", line)
        if task:
            content.append({
                "type": "taskItem",
                "attrs": {"checked": task.group(1) == "x"},
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": task.group(2)}]}],
            })
            continue

        if re.match(r"^-{3,}$", line):
            content.append({"type": "horizontalRule"})
            continue

        if line.strip():
            content.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": line}],
            })
        else:
            content.append({"type": "paragraph"})

    return content


# ── CLI ──────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="mymind",
        description="Unofficial mymind CLI & API",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("login", help="Open browser to sign in to mymind")
    sub.add_parser("logout", help="Remove saved tokens")
    sub.add_parser("test", help="Test your connection")
    sub.add_parser("list", help="List all cards")

    search_p = sub.add_parser("search", help="Search cards")
    search_p.add_argument("query", help="Search query")

    note_p = sub.add_parser("note", help="Create a note")
    note_p.add_argument("content", help="Note content (markdown)")
    note_p.add_argument("-t", "--title", default="", help="Note title")
    note_p.add_argument("--tags", default="", help="Comma-separated tags")

    url_p = sub.add_parser("save", help="Save a URL")
    url_p.add_argument("url", help="URL to save")
    url_p.add_argument("--tags", default="", help="Comma-separated tags")

    del_p = sub.add_parser("delete", help="Delete a card")
    del_p.add_argument("slug", help="Card slug/id")

    tag_p = sub.add_parser("tag", help="Add a tag to a card")
    tag_p.add_argument("slug", help="Card slug/id")
    tag_p.add_argument("tag_name", help="Tag to add")

    args = parser.parse_args()

    if args.command == "login":
        print("Opening browser — sign in to your mymind account...")
        tokens = browser_login()
        _store_tokens(**tokens)
        print("Logged in! Tokens saved to system keychain.")

    elif args.command == "logout":
        _clear_tokens()
        print("Tokens removed from keychain.")

    elif args.command == "test":
        mind = MyMind()
        if mind.test_connection():
            print("Connected to mymind!")
        else:
            print("Connection failed. Run: mymind login")

    elif args.command == "list":
        mind = MyMind()
        cards = mind.get_all_cards()
        for c in cards:
            tags_str = f" [{', '.join(c.tags)}]" if c.tags else ""
            print(f"  {c.slug}  {c.title or '(untitled)'}{tags_str}")
        print(f"\n{len(cards)} cards total")

    elif args.command == "search":
        mind = MyMind()
        results = mind.search(args.query)
        matches = results.get("matches", [])
        if not matches:
            print("No results.")
        else:
            all_cards = mind.get_all_cards()
            card_map = {c.slug: c for c in all_cards}
            for m in matches:
                c = card_map.get(m["id"])
                if c:
                    tags_str = f" [{', '.join(c.tags)}]" if c.tags else ""
                    print(f"  {c.slug}  {c.title or '(untitled)'}{tags_str}")
                else:
                    print(f"  {m['id']}  (card not found)")
            print(f"\n{len(matches)} results")

    elif args.command == "note":
        mind = MyMind()
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
        result = mind.create_note(args.content, title=args.title, tags=tags)
        print(f"Created: {result}")

    elif args.command == "save":
        mind = MyMind()
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
        result = mind.save_url(args.url, tags=tags)
        print(f"Saved: {result}")

    elif args.command == "delete":
        mind = MyMind()
        mind.delete_card(args.slug)
        print("Deleted.")

    elif args.command == "tag":
        mind = MyMind()
        mind.add_tag(args.slug, args.tag_name)
        print(f"Tagged '{args.slug}' with '{args.tag_name}'")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
