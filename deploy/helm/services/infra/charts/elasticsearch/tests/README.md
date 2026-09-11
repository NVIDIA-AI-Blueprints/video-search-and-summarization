# Elasticsearch scale acceptance test

`scale_e2e.py` reproduces the Elasticsearch portion of the NVBug 6661431
40X workload. By default it runs two waves of 1,280 per-stream indices and
80 raw-event documents per stream (102,400 indexing operations per wave).

Run it against the HTTP endpoint of a deployed four-node chart:

```bash
python3 scale_e2e.py --url http://127.0.0.1:9200
```

The test passes only when all documents are searchable, every primary shard
is `STARTED` and distributed across all four nodes, the write-rejection delta
is zero, and maximum JVM heap remains below 80%. Test indices and the temporary
index template use unique names and are removed at the end of the run.
