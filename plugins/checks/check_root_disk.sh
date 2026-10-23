#!/bin/sh
# A Monitoring Plugins check (io-diagnostics plugins.d/check_root_disk.toml): exit 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
used=$(df -P / 2>/dev/null | awk 'NR == 2 { sub("%", "", $5); print $5 }')
case "$used" in ""|*[!0-9]*) echo "DISK UNKNOWN - df -P / printed no usage"; exit 3;; esac
if [ "$used" -gt 99 ]; then state=CRITICAL; code=2; elif [ "$used" -gt 95 ]; then state=WARNING; code=1; else state=OK; code=0; fi
echo "DISK $state - / ${used}% used | '/ used'=${used}%;95;99;0;100"; exit $code
