#!/bin/sh
# Keeps one compressed dump of the database per day in /backups, the newest BACKUP_KEEP of them.
# Runs in the backup service of docker-compose.yml.
set -eu

BACKUP_DIR=/backups
# Number of daily dumps kept
BACKUP_KEEP=7
# Time between two checks for the dump of the day, in seconds
CHECK_INTERVAL_SECONDS=3600

log() {
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) backup: $*"
}

# Every dump, oldest first, as the date in the file name sorts by time
list_dumps() {
    find "$BACKUP_DIR" -maxdepth 1 -name "${MONGO_DB_NAME}-*.archive.gz" | sort
}

# Writes the dump of the day unless it exists or the free space is below twice the newest dump. The dump goes to a
# partial file first, so a failed dump leaves no file that would count as the dump of the day.
dump_today() {
    target="$BACKUP_DIR/${MONGO_DB_NAME}-$(date -u +%Y-%m-%d).archive.gz"
    if [ -e "$target" ]; then
        return 0
    fi
    newest=$(list_dumps | tail -n 1)
    if [ -n "$newest" ]; then
        needed_kb=$(($(stat -c %s "$newest") * 2 / 1024))
        free_kb=$(df -Pk "$BACKUP_DIR" | awk 'NR == 2 { print $4 }')
        if [ "$free_kb" -lt "$needed_kb" ]; then
            log "ERROR: ${free_kb} KB free, ${needed_kb} KB needed; no dump today, the existing dumps stay"
            return 0
        fi
    fi
    if mongodump --quiet --host "${MONGO_HOST:-mongo}" --username "$MONGO_USER" --password "$MONGO_PASS" \
        --authenticationDatabase admin --db "$MONGO_DB_NAME" --gzip --archive="$target.partial"; then
        mv "$target.partial" "$target"
        log "wrote $target ($(stat -c %s "$target") bytes)"
    else
        rm -f "$target.partial"
        log "ERROR: mongodump failed; next try in ${CHECK_INTERVAL_SECONDS} s"
    fi
}

# Removes all but the newest BACKUP_KEEP dumps
prune() {
    list_dumps | head -n "-$BACKUP_KEEP" | while read -r old_dump; do
        rm -f "$old_dump"
        log "removed $old_dump"
    done
}

# docker stop sends SIGTERM; sleeping in the background lets the trap end the script at once
trap 'exit 0' TERM
while true; do
    dump_today
    prune
    sleep "$CHECK_INTERVAL_SECONDS" &
    wait $!
done
