# Operations

How the Docker Compose stack is run, updated, backed up and restored. Commands run in the project root.

## Start, stop, update

```bash
docker compose up --build -d     # build and start every service
docker compose down              # stop every service; the database volume and the dumps stay
```

An update pulls the code and rebuilds the images; `docker compose pull` fetches new patch releases of the MongoDB and Mongo Express images:

```bash
git pull
docker compose pull
docker compose up --build -d
```

Every service restarts after a crash and after a restart of Docker (`restart: always`). A new major release of MongoDB changes the data format of the database volume, which the older release cannot read again, so its tag in `docker-compose.yml` is raised only after a backup.

## Status and logs

`docker compose ps` shows whether each service is healthy:

| Service | Healthy when |
| --- | --- |
| `pipeline-service`, `metadata-worker` | the main loop touched its heartbeat file within the last 5 minutes |
| `dashboard`, `frontend` | the site or the Streamlit app answers |
| `mongo` | the database answers a ping |
| `backup` | the newest dump is younger than 2 days |

`docker compose logs -f {service}` follows the log of a service. Docker keeps five log files of 10 MB per container. `LOG_LEVEL` in `.env` sets the lowest level written (`INFO` by default, `DEBUG` adds every routine step) and `LOG_FORMAT=json` writes one JSON object per line, with fields such as `feed` and `job_id`, for a log collector.

At level `INFO` the pipeline service writes one summary per feed and hour (runs, failures, inserted and deleted documents, skipped chunks, gaps). A failure that repeats with the same cause is logged as warning once, its repeats at level `DEBUG`, and its end as `works again after {n} failures`.

The "Data updates" panel on the overview page of the dashboard shows the state of each feed: its last run, the runs with gaps or failures and their causes. A job that reports no progress for 30 minutes is ended by the watchdog of the pipeline service (see [Pipeline Service](pipeline-service.md#failures)).

## Resources

| What | Where |
| --- | --- |
| Database | volume `mongo-data` |
| Local data files, image frames, magnetic field models | `data/` |
| Database dumps, seven kept | `backups/` (`BACKUP_DIR`) |
| Images and build cache, growing with every build | Docker |

The pipeline service may use up to 3 GB of memory (`mem_limit`), its peak is about 2.4 GB while the coronal magnetic field model is computed. Old images and build cache are removed with:

```bash
docker image prune
docker builder prune
```

## Backup

The `backup` service runs [scripts/backup.sh](../scripts/backup.sh): every hour it checks for the dump of the day and, if missing, writes `space_weather-{date}.archive.gz` (`mongodump --gzip --archive`) to `backups/`. It keeps the newest seven dumps. While the free space is below twice the size of the newest dump, it writes no dump and logs an error, so the existing dumps stay. A failed dump leaves no file and is tried again an hour later.

`BACKUP_DIR` in `.env` moves the dumps to another directory, which must be writable for uid 1000.

## Restore

1. Stop the services that write to the database:

   ```bash
   docker compose stop pipeline-service metadata-worker frontend dashboard
   ```

2. Restore a dump; `--drop` replaces each collection by its state in the dump:

   ```bash
   docker compose run --rm --entrypoint sh backup -c 'mongorestore --host mongo --username "$MONGO_USER" --password "$MONGO_PASS" --authenticationDatabase admin --gzip --drop --archive=/backups/space_weather-{date}.archive.gz'
   ```

3. Start the services again with `docker compose up -d`. The pipeline service requeues the jobs left running in the dump and fetches what is missing since the dump.
