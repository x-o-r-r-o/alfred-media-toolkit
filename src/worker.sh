#!/bin/bash
# Background queue for video and audio conversions (ffmpeg, avconvert or afconvert), one file at a time.
# Jobs are files in $cache/queue written by media.js enqueue(): NUL-terminated fields
#   batch, index, count, label, source, final path, temp path, replace (0/1), reveal (0/1), tool, argv…
# Usage: worker.sh            process the queue (exits at once if another worker is running)
#        worker.sh --cancel   stop the running conversion and clear the queue
cache="${alfred_workflow_cache:-${TMPDIR:-/tmp}/media-toolkit}"
queue="$cache/queue"
lock="$cache/worker.lock"
log="$cache/conversions.log"
progress="$cache/progress.txt"
batches="$cache/batches"
mkdir -p "$queue" "$batches"

alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

notify() {
  if [ -n "$MT_TEST_NOTIFY_FILE" ]; then
    printf '%s\n' "$1" >>"$MT_TEST_NOTIFY_FILE"
    return
  fi
  /usr/bin/osascript -l JavaScript \
    -e 'function run(a) { Application("com.runningwithcrayons.Alfred").runTrigger("notify", { inWorkflow: a[0], withArgument: a[1] }) }' \
    "$alfred_workflow_bundleid" "$1" >/dev/null 2>&1
}

reveal() {
  if [ -n "$MT_TEST_REVEAL_FILE" ]; then
    printf '%s\n' "$@" >"$MT_TEST_REVEAL_FILE"
    return
  fi
  /usr/bin/osascript -l JavaScript \
    -e 'ObjC.import("AppKit"); function run(a) { $.NSWorkspace.sharedWorkspace.activateFileViewerSelectingURLs($(a.map((p) => $.NSURL.fileURLWithPath(p)))) }' \
    "$@" >/dev/null 2>&1
}

if [ "$1" = "--cancel" ]; then
  rm -f "$queue"/*.job
  if [ -d "$lock" ] && alive "$(cat "$lock/pid" 2>/dev/null)"; then
    touch "$lock/cancel"
    child=$(cat "$lock/child" 2>/dev/null)
    alive "$child" && kill "$child" 2>/dev/null
    echo "Cancelled the conversions"
  else
    echo "Nothing to cancel"
  fi
  exit 0
fi

# One worker at a time. A lock left behind by a crash or a restart (dead pid) is taken over.
acquire() {
  if mkdir "$lock" 2>/dev/null; then
    echo $$ >"$lock/pid"
    return 0
  fi
  local pid age
  pid=$(cat "$lock/pid" 2>/dev/null)
  if [ -z "$pid" ]; then
    # Another worker may be between mkdir and writing its pid: only steal an old, empty lock
    age=$(($(date +%s) - $(stat -f %m "$lock" 2>/dev/null || date +%s)))
    [ "$age" -lt 10 ] && return 1
  elif alive "$pid"; then
    return 1
  fi
  rm -rf "$lock"
  mkdir "$lock" 2>/dev/null || return 1
  echo $$ >"$lock/pid"
}

# Move the finished temp file into place without clobbering (unless it replaces the original on purpose).
commit() {
  local tmp="$1" final="$2" replace="$3"
  if [ "$replace" = 1 ]; then
    mv -f "$tmp" "$final" && printf '%s' "$final"
    return
  fi
  local dir="${final%/*}" name="${final##*/}" stem ext n=2 target="$final"
  if [[ "$name" == ?*.* ]]; then
    stem="${name%.*}"
    ext=".${name##*.}"
  else
    stem="$name"
    ext=""
  fi
  while [ -e "$target" ]; do
    target="$dir/$stem-$n$ext"
    n=$((n + 1))
  done
  mv -n "$tmp" "$target" && [ ! -e "$tmp" ] && printf '%s' "$target"
}

record() { printf '%s\0' "$@" >>"$batches/$1"; }

finish() {
  local batch="$1" rev="$2" f=() x i ok=() oklabels=() fails=0 first=""
  while IFS= read -r -d '' x; do f+=("$x"); done <"$batches/$batch"
  rm -f "$batches/$batch"
  for ((i = 0; i + 3 < ${#f[@]}; i += 4)); do
    if [ "${f[i + 1]}" = ok ]; then
      oklabels+=("${f[i + 2]}")
      ok+=("${f[i + 3]}")
    else
      fails=$((fails + 1))
      [ -z "$first" ] && first="${f[i + 2]}: ${f[i + 3]}"
    fi
  done
  local n=${#ok[@]} msg
  if [ "$n" -eq 1 ] && [ "$fails" -eq 0 ]; then
    msg="Converted ${oklabels[0]} → ${ok[0]##*/}"
  elif [ "$fails" -eq 0 ]; then
    msg="Converted $n files"
  elif [ "$n" -eq 0 ] && [ "$fails" -eq 1 ]; then
    msg="Failed: $first"
  elif [ "$n" -eq 0 ]; then
    msg="All $fails conversions failed · $first"
  else
    msg="$n converted, $fails failed · $first"
  fi
  notify "$msg"
  if [ "$rev" = 1 ] && [ "$n" -gt 0 ]; then reveal "${ok[@]}"; fi
}

run_job() {
  local f=() x
  while IFS= read -r -d '' x; do f+=("$x"); done <"$1"
  rm -f "$1"
  [ "${#f[@]}" -ge 11 ] || return
  local batch="${f[0]}" idx="${f[1]}" count="${f[2]}" label="${f[3]//[$'\n\t']/ }" src="${f[4]}" final="${f[5]}"
  local tmp="${f[6]}" replace="${f[7]}" rev="${f[8]}" tool="${f[9]}" cmd=("${f[@]:10}")
  local duration=0 probe rc child dest reason
  if [ "$tool" = ffmpeg ]; then
    probe="$(dirname "${cmd[0]}")/ffprobe"
    if [ -x "$probe" ]; then
      duration=$("$probe" -v error -show_entries format=duration -of default=nw=1:nk=1 "file:$src" 2>/dev/null | head -1)
    fi
  fi
  printf '%s\n%s\n%s\n' "${duration:-0}" "$label" "$tool" >"$lock/state"
  : >"$progress"
  { printf '\n== %s  %s (%s of %s)\n' "$(date '+%F %T')" "$label" "$idx" "$count"; printf '%q ' "${cmd[@]}"; echo; } >>"$log"
  if [ ! -e "$src" ]; then
    record "$batch" fail "$label" "file not found"
  else
    if [ "$tool" = avconvert ]; then
      "${cmd[@]}" >"$progress" 2>"$cache/last.err" &
    else
      "${cmd[@]}" >>"$log" 2>"$cache/last.err" &
    fi
    child=$!
    echo "$child" >"$lock/child"
    wait "$child"
    rc=$?
    rm -f "$lock/child"
    cat "$cache/last.err" >>"$log" 2>/dev/null
    if [ -e "$lock/cancel" ]; then
      rm -f "$tmp"
      return
    fi
    if [ "$rc" -eq 0 ] && [ -s "$tmp" ] && dest=$(commit "$tmp" "$final" "$replace") && [ -n "$dest" ]; then
      record "$batch" ok "$label" "$dest"
      echo "-> $dest" >>"$log"
    else
      rm -f "$tmp"
      # The most specific line: the first that looks like an error, else the last one
      reason=$(grep -i -m1 -E 'error|invalid|fail|unable|cannot|no such|not |unknown|denied' "$cache/last.err" 2>/dev/null)
      [ -z "$reason" ] && reason=$(grep -v '^[[:space:]]*$' "$cache/last.err" 2>/dev/null | tail -1)
      reason=$(printf '%s' "$reason" | sed -E 's/^avconvert: +//; s/ (--|with) file:.*//; s/^[[:space:]]+//' | cut -c1-160)
      case "$reason" in
        "invalid configuration"*)
          reason="avconvert can't make this format from this file"
          [[ "$final" == *.m4a ]] && reason="$reason (no audio track?)"
          ;;
      esac
      [ -z "$reason" ] && reason="exit code $rc"
      [ "$rc" -eq 0 ] && reason="could not save the result"
      record "$batch" fail "$label" "$reason"
      echo "FAILED: $reason" >>"$log"
    fi
  fi
  [ "$idx" = "$count" ] && finish "$batch" "$rev"
}

acquire || exit 0
trap 'rm -rf "$lock"' EXIT
# Keep the log small
if [ -f "$log" ] && [ "$(stat -f %z "$log")" -gt 5000000 ]; then
  tail -c 1000000 "$log" >"$log.tmp" && mv -f "$log.tmp" "$log"
fi

while :; do
  job=""
  for j in "$queue"/*.job; do
    [ -e "$j" ] && job="$j" && break
  done
  if [ -z "$job" ]; then
    if [ -e "$lock/cancel" ]; then
      rm -f "$lock/cancel" "$batches"/*
      notify "Cancelled the conversions"
    fi
    rm -rf "$lock"
    trap - EXIT
    # A job may have arrived between the check and the unlock: pick it up
    for j in "$queue"/*.job; do
      [ -e "$j" ] && acquire && trap 'rm -rf "$lock"' EXIT && continue 2
    done
    rm -f "$progress"
    exit 0
  fi
  if [ -e "$lock/cancel" ]; then
    rm -f "$job"
    continue
  fi
  run_job "$job"
done
