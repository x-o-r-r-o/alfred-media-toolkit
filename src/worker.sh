#!/bin/bash
# Background queue for video and audio conversions (ffmpeg, avconvert or afconvert), one file at a time.
# Jobs are files in $cache/queue written by media.js enqueue(): NUL-terminated fields
#   batch, index, count, label, source, final path, temp path, replace (0/1), reveal (0/1), tool, argv…
# The argv may hold several commands separated by a "::then::" field (GIF: palette, then the GIF itself).
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
# The pid in a stale lock (after a crash or restart) may since belong to another process
is_worker() { alive "$1" && /bin/ps -p "$1" -o command= 2>/dev/null | grep -q 'worker\.sh'; }
# A conversion is only killed if its command line still names our temp file (pids get reused). Only the
# file name is compared: it is ASCII (.mt-<pid>-<ms>-<n>.ext), while ps escapes other characters of the
# folder path when the locale isn't UTF-8, as under Alfred.
is_child() { alive "$1" && [ -n "$2" ] && /bin/ps -ww -p "$1" -o command= 2>/dev/null | grep -qF -- "${2##*/}"; }

notify() {
  if [ -n "$MT_TEST_NOTIFY_FILE" ]; then
    printf '%s\n' "$1" >>"$MT_TEST_NOTIFY_FILE"
    return
  fi
  [ -n "$MT_TEST" ] && return # the tests never reach the real Alfred
  /usr/bin/osascript -l JavaScript \
    -e 'function run(a) { Application("com.runningwithcrayons.Alfred").runTrigger("notify", { inWorkflow: a[0], withArgument: a[1] }) }' \
    "$alfred_workflow_bundleid" "$1" >/dev/null 2>&1
}

reveal() {
  if [ -n "$MT_TEST_REVEAL_FILE" ]; then
    printf '%s\n' "$@" >"$MT_TEST_REVEAL_FILE"
    return
  fi
  [ -n "$MT_TEST" ] && return
  /usr/bin/osascript -l JavaScript \
    -e 'ObjC.import("AppKit"); function run(a) { $.NSWorkspace.sharedWorkspace.activateFileViewerSelectingURLs($(a.map((p) => $.NSURL.fileURLWithPath(p)))) }' \
    "$@" >/dev/null 2>&1
}

# Remove a temp file of ours (and its helpers, like a GIF palette); never anything else
drop_tmp() {
  case "${1##*/}" in
    .mt-*) rm -f "$1" "$1"-aux* ;;
  esac
}

if [ "$1" = "--cancel" ]; then
  # Queued jobs go first, then the flag: a job queued after this point runs normally
  rm -f "$queue"/*.job
  if [ -d "$lock" ] && is_worker "$(cat "$lock/pid" 2>/dev/null)"; then
    touch "$lock/cancel"
    child=$(cat "$lock/child" 2>/dev/null)
    is_child "$child" "$(cat "$lock/tmp" 2>/dev/null)" && kill "$child" 2>/dev/null
    echo "Cancelled the conversions"
  else
    echo "Nothing to cancel"
  fi
  exit 0
fi

# One worker at a time. A lock left behind by a crash or a restart (dead pid) is taken over, after stopping
# the conversion it may have left running and removing its partial output.
acquire() {
  if mkdir "$lock" 2>/dev/null; then
    echo $$ >"$lock/pid"
    return 0
  fi
  local pid age child tmp
  pid=$(cat "$lock/pid" 2>/dev/null)
  if [ -z "$pid" ]; then
    # Another worker may be between mkdir and writing its pid: only steal an old, empty lock
    age=$(($(date +%s) - $(stat -f %m "$lock" 2>/dev/null || date +%s)))
    [ "$age" -lt 10 ] && return 1
  elif is_worker "$pid"; then
    return 1
  fi
  child=$(cat "$lock/child" 2>/dev/null)
  tmp=$(cat "$lock/tmp" 2>/dev/null)
  is_child "$child" "$tmp" && kill "$child" 2>/dev/null
  [ -n "$tmp" ] && drop_tmp "$tmp"
  rm -rf "$lock"
  mkdir "$lock" 2>/dev/null || return 1
  echo $$ >"$lock/pid"
}

# Stopped (logout, kill): stop the conversion too, rather than leave it running without us
on_term() {
  local child tmp
  child=$(cat "$lock/child" 2>/dev/null)
  tmp=$(cat "$lock/tmp" 2>/dev/null)
  is_child "$child" "$tmp" && kill "$child" 2>/dev/null
  [ -n "$tmp" ] && drop_tmp "$tmp"
  rm -rf "$lock"
  exit 143
}

# Move the finished temp file into place without clobbering (unless it replaces the original on purpose).
# The temp file is always in the destination folder, so this is a rename on the same volume.
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
  [ -e "$batches/$batch" ] || return
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

# Run one command (an argv) in the background so that --cancel can stop it. Returns its exit code.
run_cmd() {
  local rc
  if [ "$tool" = avconvert ]; then
    "$@" >"$progress" 2>>"$cache/last.err" &
  else
    "$@" >>"$log" 2>>"$cache/last.err" &
  fi
  echo "$!" >"$lock/child"
  wait "$!" 2>/dev/null
  rc=$?
  rm -f "$lock/child"
  return "$rc"
}

run_job() {
  local f=() x
  while IFS= read -r -d '' x; do f+=("$x"); done <"$1"
  rm -f "$1"
  [ "${#f[@]}" -ge 11 ] || return
  local batch="${f[0]}" idx="${f[1]}" count="${f[2]}" label="${f[3]//[$'\n\t']/ }" src="${f[4]}" final="${f[5]}"
  local tmp="${f[6]}" replace="${f[7]}" rev="${f[8]}" cmd=("${f[@]:10}")
  tool="${f[9]}"
  local duration=0 probe rc=0 dest reason part=() i
  if [ "$tool" = ffmpeg ]; then
    # Progress is out_time / duration: a trim's duration is its -t, otherwise ask ffprobe
    for ((x = 0; x + 1 < ${#cmd[@]}; x++)); do
      [ "${cmd[x]}" = "-t" ] && duration="${cmd[x + 1]}"
    done
    probe="$(dirname "${cmd[0]}")/ffprobe"
    if [ "$duration" = 0 ] && [ -x "$probe" ]; then
      duration=$("$probe" -v error -show_entries format=duration -of default=nw=1:nk=1 "file:$src" 2>/dev/null | head -1)
    fi
  fi
  printf '%s\n%s\n%s\n' "${duration:-0}" "$label" "$tool" >"$lock/state"
  printf '%s' "$tmp" >"$lock/tmp"
  : >"$progress"
  : >"$cache/last.err"
  { printf '\n== %s  %s (%s of %s)\n' "$(date '+%F %T')" "$label" "$idx" "$count"; printf '%q ' "${cmd[@]}"; echo; } >>"$log"
  if [ -e "$lock/cancel" ]; then
    return # cancelled before it started
  elif [ ! -e "$src" ]; then
    record "$batch" fail "$label" "file not found"
  else
    # The commands of the job, one after the other
    for ((i = 0; i <= ${#cmd[@]}; i++)); do
      if [ "$i" -eq "${#cmd[@]}" ] || [ "${cmd[i]}" = "::then::" ]; then
        if [ "${#part[@]}" -gt 0 ]; then
          run_cmd "${part[@]}"
          rc=$?
          [ "$rc" -ne 0 ] || [ -e "$lock/cancel" ] && break
        fi
        part=()
      else
        part+=("${cmd[i]}")
      fi
    done
    cat "$cache/last.err" >>"$log" 2>/dev/null
    rm -f "$tmp"-aux*
    if [ -e "$lock/cancel" ]; then
      drop_tmp "$tmp"
      return
    fi
    if [ "$rc" -eq 0 ] && [ -s "$tmp" ] && dest=$(commit "$tmp" "$final" "$replace") && [ -n "$dest" ]; then
      record "$batch" ok "$label" "$dest"
      echo "-> $dest" >>"$log"
    else
      drop_tmp "$tmp"
      # The most specific line: the first that looks like an error, else the last one
      reason=$(grep -i -m1 -E 'error|invalid|fail|unable|cannot|no such|not |unknown|denied|matches no streams' "$cache/last.err" 2>/dev/null)
      [ -z "$reason" ] && reason=$(grep -v '^[[:space:]]*$' "$cache/last.err" 2>/dev/null | tail -1)
      reason=$(printf '%s' "$reason" | sed -E 's/^avconvert: +//; s/ (--|with) file:.*//; s/^[[:space:]]+//' | cut -c1-160)
      case "$reason" in
        "invalid configuration"*)
          reason="avconvert can't make this format from this file"
          [[ "$final" == *.m4a ]] && reason="$reason (no audio track?)"
          ;;
        *"map '0:a"*"matches no streams"*) reason="no audio track" ;;
        *"map '0:V"*"matches no streams"* | *"map '0:v"*"matches no streams"*) reason="no video track" ;;
      esac
      [ -z "$reason" ] && reason="exit code $rc"
      [ "$rc" -eq 0 ] && reason="could not save the result"
      record "$batch" fail "$label" "$reason"
      echo "FAILED: $reason" >>"$log"
    fi
  fi
  rm -f "$lock/tmp"
  [ "$idx" = "$count" ] && finish "$batch" "$rev"
}

# A cancel request: the running job has stopped; forget the cancelled batches. No notification: the
# cancel action already showed one ("Cancelled the conversions")
handle_cancel() {
  rm -f "$lock/cancel" "$batches"/*
}

acquire || exit 0
trap 'rm -rf "$lock"' EXIT
trap on_term TERM INT HUP
# Keep the log small, and drop what a crash left behind
if [ -f "$log" ] && [ "$(stat -f %z "$log")" -gt 5000000 ]; then
  tail -c 1000000 "$log" >"$log.tmp" && mv -f "$log.tmp" "$log"
fi
find "$batches" "$queue" -type f -mtime +1 -delete 2>/dev/null

while :; do
  [ -e "$lock/cancel" ] && handle_cancel
  # Claim the next job with a rename, so that no two workers ever run the same one
  job=""
  for j in "$queue"/*.job; do
    [ -e "$j" ] || continue
    if mv "$j" "${j%.job}.run$$" 2>/dev/null; then
      job="${j%.job}.run$$"
      break
    fi
  done
  if [ -z "$job" ]; then
    rm -rf "$lock"
    trap - EXIT
    # A job may have arrived between the check and the unlock: pick it up
    for j in "$queue"/*.job; do
      [ -e "$j" ] && acquire && trap 'rm -rf "$lock"' EXIT && continue 2
    done
    rm -f "$progress"
    exit 0
  fi
  run_job "$job"
done
