# Vibe Coding Scraper (+ Parser)

A lightweight scraping-and-parsing pipeline for collecting, parsing, and structuring vibe-coding chat-history traces (e.g., Cursor, GitHub Copilot, Claude Code) exported from [SpecStory](https://specstory.com/), along with related GitHub repository metadata for research.

## Data Structure

All data is stored in the `data/` folder. A `metadata.json` file records basic metadata (e.g., scrape date, author).

### 1) Search chat-history files

**`searches/`**: Search results saved as `{prefix}.json`, using queries like `.specstory/history/{prefix} in:path language:markdown`. `prefix` is a date string (e.g., `2025-01`, `2024-04-0`) split to bypass GitHub’s 1000-result path-search limit. This differs from GitHub UI query grammar: `path:.specstory/history/ language:markdown` ([link](https://github.com/search?q=path%3A.specstory%2Fhistory%2F%20language%3Amarkdown&type=code)).

**`searches.json`**: Combined records from `searches/`; `sha` is the SHA of the target chat-history Markdown file.

### 2) Repository-level metadata

**`repositories.json`**: Repository metadata from `searches.json`, fetched via `https://api.github.com/repos/{full_name}`.

**`mapping.csv`**: Session-to-repository mapping table. Use this table for any future analysis that requires session-repository mapping, because deduplication is performed by session SHA during scraping, and mapping directly from `searches.json` may be inconsistent with the ground truth.

**`contributors/`**: Repository contributor lists from `https://api.github.com/repos/{full_name}/contributors`, saved as `{id}.json`.

**`languages.json`**: Repository language stats from `https://api.github.com/repos/{full_name}/languages`.

**`readmes/`**: README Markdown decoded directly from `https://api.github.com/repos/{full_name}/readme`, saved as `{id}.md`.

**`file_trees/`**: Recursive file trees from each repo default branch via `https://api.github.com/repos/{full_name}/git/trees/{default_branch}?recursive=1`, saved as `{id}.json`.

**`commits_history/`**: Full repository commit histories from `https://api.github.com/repos/{full_name}/commits`, saved as `{id}.json`.

### 3) Chat file contents and markdown

**`contents/`**: Chat-history blob content from each result’s `git_url` (`https://api.github.com/repositories/{id}/git/blobs/{sha}`), stored as base64 in `{sha}`.

**`markdowns/`**: Decoded chat-history Markdown files from `contents/`, saved as `{sha}.md` (core analysis data).

### 4) Commit-level traces and snapshots

**`commits_path/`**: Commit lists filtered by chat file path from `https://api.github.com/repos/{full_name}/commits?path={path}`, saved as `{sha}` (JSON).

**`commits/`**: Detailed commit payloads (including file patches) for commits in `commits_path/`, fetched from `https://api.github.com/repos/{full_name}/commits/{commit_sha}` and saved as `{commit_sha}.json`.

> **Note:** `https://api.github.com/repos/{full_name}/zipball/{commit_sha}` downloads a full repository snapshot as a ZIP file at a specific commit, so `git clone` + `git checkout` is unnecessary.

### 5) Parsed chat outputs

**`markdowns_cli/`**: Markdown sessions identified as CLI-agent style traces (e.g., Claude Code sessions). These files are copied from `markdowns/` and excluded from structured parsing because their format differs from the standard chat-history schema.

**`parsed_chats/`**: Structured chat records parsed from standard files in `markdowns/`, saved as `{sha}.md.json`. Each file contains:
- `title`: extracted from markdown headings (with fallbacks).
- `timestamp`: derived from the original search result `name` when possible.
- `platform`: currently fixed as `Cursor / Copilot`.
- `messages`: list of role-based messages (`User` / `Assistant` / `Agent`) where each message stores `blocks` (content split into text/code/details/tool-use/read-file/think segments).

**`parsed_chats_simple/`**: Simplified standard-chat output from `parsed_chats/`, keeping the same top-level metadata (`title`, `timestamp`, `platform`) but storing each message as a plain `{role, content}` pair (without nested content-block parsing).

**`parsed_chats_simple_cli/`**: Simplified output parsed from `markdowns_cli/` via `parse_cli.py`, storing `{role, content}` (plus optional metadata for some formats). Role splitting (`User` / `Assistant`) is heuristic on exported chats.
