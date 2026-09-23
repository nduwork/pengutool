#!/usr/bin/env bash
# A repository chain is shared by sessions: never erase it when another session starts.
# Restore tracking instructions on startup, resume, clear and compact.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/hook_prompt.sh"
