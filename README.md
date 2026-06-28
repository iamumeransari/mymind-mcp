# mymind MCP

<img width="1920" height="1080" alt="Listing Screenshot" src="https://github.com/user-attachments/assets/c9e50fe8-d94a-4da3-beb6-e1c9482abe11" />

‎<p></p>
‎
Community-built MCP for mymind.
Works with Claude Code, Cursor, Claude Desktop, Obsidian, and any MCP-compatible AI tool.

> Community-built. Not affiliated with mymind.

## Why?

Your AI only knows what you tell it.

Your mymind already contains years of articles, bookmarks, highlights, notes, and ideas you've collected. This project gives AI direct access to that knowledge so it can search, read, organize, and build on what you've already saved.

## Features

- Search your entire mymind
- Read cards, notes, articles, bookmarks, and highlights
- Create notes and save URLs
- Manage tags and Spaces
- Works with any MCP-compatible client
- Includes a Python SDK and CLI

## Install

```bash
pip install mymind-api
```

Authenticate:

```bash
mymind login
```

Add to Claude Code:

```bash
claude mcp add --transport stdio mymind -- mymind-mcp
```

## Examples

> "Find every article I saved about startup pricing."

> "Summarize everything I've bookmarked about product strategy."

> "Show me every tweet I saved from Paul Graham."

> "Save this conversation to mymind."

## Documentation

Full documentation for the Python SDK, CLI, MCP server, authentication, and API is available in the `/docs` directory.

## Disclaimer

mymind does not currently provide a public API.

This project is a community-built implementation built on top of mymind's internal endpoints. It may require updates if those endpoints change.
