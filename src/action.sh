#!/bin/bash
# Runs the operation picked in a Media Toolkit Script Filter.
#   $1        the item's arg (operation id, or text to copy)
#   $mt_op    the operation id; $mt_files the files as a JSON array; $mt_reveal 1 to reveal results
# Files never pass through a shell string: media.js reads them from the environment.
cd "$(dirname "$0")" || exit 1
cache="${alfred_workflow_cache:-${TMPDIR:-/tmp}/media-toolkit}"
op="${mt_op:-$1}"
# osascript errors are kept for the log action; keep the file small
errors="$cache/errors.log"
if [ -f "$errors" ] && [ "$(stat -f %z "$errors")" -gt 1000000 ]; then
  tail -c 200000 "$errors" >"$errors.tmp" && mv -f "$errors.tmp" "$errors"
fi

case "$op" in
  copy)
    # MT_TEST (the tests): never the real clipboard
    if [ -n "$MT_TEST_CLIPBOARD_FILE" ]; then printf '%s' "$1" >"$MT_TEST_CLIPBOARD_FILE"; elif [ -z "$MT_TEST" ]; then printf '%s' "$1" | /usr/bin/pbcopy; fi
    echo "Copied “$1”: paste it into Terminal"
    ;;
  cancel)
    # The worker stops quietly: this is the one notification
    ./worker.sh --cancel
    ;;
  log)
    log="$cache/conversions.log"
    [ -e "$log" ] || log="$cache/media-toolkit.log"
    if [ ! -e "$log" ]; then
      echo "No log yet"
    elif [ -n "$MT_TEST_REVEAL_FILE" ]; then
      printf '%s\n' "$log" >"$MT_TEST_REVEAL_FILE"
    elif [ -z "$MT_TEST" ]; then
      /usr/bin/open -R "$log"
    fi
    ;;
  resize:* | convert:* | rotate:* | flip:* | strip:* | optimize | removebg | removebg:*)
    mkdir -p "$cache"
    msg=$(/usr/bin/osascript -l JavaScript ./media.js apply "$op" 2>>"$errors")
    echo "${msg:-Media Toolkit couldn’t finish: see $errors}"
    ;;
  mp4 | hevc | webm | mov | gif | compress:* | scale:* | mute | mp3 | m4a | wav | flac | aiff | trim:*)
    mkdir -p "$cache"
    msg=$(/usr/bin/osascript -l JavaScript ./media.js enqueue "$op" 2>>"$errors")
    msg="${msg:-Media Toolkit couldn’t finish: see $errors}"
    case "$msg" in
      Queued*)
        if [ -n "$MT_TEST_WORKER_FOREGROUND" ]; then
          ./worker.sh
        else
          # set -m: its own process group, so it outlives Alfred ending this script's group
          set -m
          nohup ./worker.sh </dev/null >/dev/null 2>&1 &
        fi
        ;;
    esac
    echo "$msg"
    ;;
  *)
    echo "Unknown operation: $op"
    ;;
esac
