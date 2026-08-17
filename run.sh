#!/bin/sh
cd ~/devel/key-mapper/ || exit 1

# Two live remappers tap the same keystrokes, so ctrl+c would arrive as cmd+c at
# the first tap and get remapped again by the second. Refuse to start instead.
# Pattern excludes this script (its argv holds "key-mapper", not "keymapper").
running=$(pgrep -f '\.venv/bin/keymapper run' 2>/dev/null)
if [ -n "$running" ]; then
    echo "keymapper is already running:" >&2
    ps -o pid=,lstart=,command= -p $(echo "$running" | tr '\n' ',' | sed 's/,$//') >&2
    echo "" >&2
    echo "stop it first:  kill $(echo "$running" | tr '\n' ' ')" >&2
    exit 1
fi

./.venv/bin/keymapper doctor
echo "--------"
echo ""
./.venv/bin/keymapper run