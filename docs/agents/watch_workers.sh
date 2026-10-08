#!/bin/bash
# usage: watch_workers.sh <report-dir> <agent-name>...
# Exits 0 on the first report-<agent>.md; 1 when a worker is blocked, gone, or idle 10 minutes without one.
dir=$1; shift
idle_minutes=10
declare -A idle
while true; do
  for name in "$@"; do
    report="$dir/report-$name.md"
    [ -f "$report" ] && { echo "REPORT $name $report"; exit 0; }
    status=$(herdr agent get "$name" 2>&1 | grep -o '"agent_status":"[a-z]*"')
    [ -z "$status" ] && { echo "GONE $name"; exit 1; }
    [[ $status == *blocked* ]] && { echo "BLOCKED $name"; herdr agent read "$name" --source visible | tail -30; exit 1; }
    # agy shows idle while it waits on its own subagents
    if [[ $status == *working* ]] || herdr agent read "$name" --source visible 2>/dev/null | grep -q 'subagent(s)'; then
      idle[$name]=0
    else
      idle[$name]=$(( ${idle[$name]:-0} + 1 ))
    fi
    if (( ${idle[$name]} >= idle_minutes )); then
      echo "ATTENTION $name $status"
      herdr agent read "$name" --source visible | tail -30
      exit 1
    fi
  done
  sleep 60
done
