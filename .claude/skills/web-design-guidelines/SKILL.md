---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

# Web Interface Guidelines

Review files for compliance with Web Interface Guidelines.

## How It Works

1. Read the pinned guidelines in `guidelines.md` (this folder)
2. Read the specified files (or prompt user for files/pattern)
3. Check against all rules in the guidelines
4. Output findings in the terse `file:line` format

## Guidelines Source

Local, pinned copy: `guidelines.md` in this folder (vercel-labs/web-interface-guidelines
`command.md` at commit 434b7f913646, MIT, see `LICENSE`). Do not fetch the upstream URL;
this project pins vendored skills (see `../VENDORED.md`). Ignore the `$ARGUMENTS` line in it;
the files to review come from the user's request.

## Usage

When a user provides a file or pattern argument:
1. Read `guidelines.md` in this folder
2. Read the specified files
3. Apply all rules from the guidelines
4. Output findings using the format specified in the guidelines

If no files specified, ask the user which files to review.
