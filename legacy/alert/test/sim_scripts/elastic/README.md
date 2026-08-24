# Elastic Simulator

Provides a lightweight HTTP simulator for Elasticsearch operations used by the
alert agent. The simulator covers basic index management and document indexing
endpoints required by `ElasticClient`.

## Endpoints

- `GET /`, `HEAD /`: health ping
- `GET /health`: status information
- `GET /status`: dump indices and document counts
- `HEAD /<index>`: check whether the index exists
- `PUT /<index>`: create or update index metadata
- `DELETE /<index>`: remove an index
- `POST /<index>/_doc` and `POST|PUT /<index>/_doc/<id>`: store documents

## Running

```bash
export ELASTIC_SIM_PORT=9200
python -m test.sim_scripts.elastic.elastic_sim
```

## Notes

- Documents are stored in-memory only
- Unsupported refresh values return HTTP 400
- Index creation returns 201 the first time, 200 afterward

