#!/bin/bash
# Runs the operation picked in a Media Toolkit Script Filter.
#   $1        the item's arg (operation id, or text to copy)
#   $mt_op    the operation id; $mt_files the files as a JSON array; $mt_reveal 1 to reveal results
# Files never pass through a shell string: media.js reads them from the environment.
cd "$(dirname "$0")" || exit 1
cache="${alfred_workflow_cache:-${TMPDIR:-/tmp}/media-toolkit}"
op="${mt_op:-$1}"

case "$op" in
  copy)
    printf '%s' "$1" | /usr/bin/pbcopy
    echo "Copied “$1”: paste it into Terminal"
    ;;
  cancel)
    ./worker.sh --cancel
    ;;
  log)
    log="$cache/conversions.log"
    [ -e "$log" ] || log="$cache/media-toolkit.log"
    if [ -e "$log" ]; then /usr/bin/open -R "$log"; else echo "No log yet"; fi
    ;;
  resize:* | convert:* | rotate:* | flip:* | strip:* | optimize | removebg | removebg:*)
    mkdir -p "$cache"
    msg=$(/usr/bin/osascript -l JavaScript ./media.js apply "$op" 2>>"$cache/errors.log")
    echo "${msg:-Media Toolkit failed: see $cache/errors.log}"
    ;;
  mp4 | hevc | webm | mov | gif | compress:* | scale:* | mute | mp3 | m4a | wav | flac | aiff | trim:*)
    mkdir -p "$cache"
    msg=$(/usr/bin/osascript -l JavaScript ./media.js enqueue "$op" 2>>"$cache/errors.log")
    msg="${msg:-Media Toolkit failed: see $cache/errors.log}"
    case "$msg" in
      Queued*)
        if [ -n "$MT_TEST_WORKER_FOREGROUND" ]; then
          ./worker.sh
        else
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
