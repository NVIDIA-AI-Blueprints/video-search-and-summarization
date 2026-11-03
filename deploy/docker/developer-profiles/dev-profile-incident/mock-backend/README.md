# mock-backend

Two independent zero-GPU mock backends for local development; each is its own `uv` project.

- [`base_profile_mock/`](base_profile_mock/README.md) (:7777): the agent, VST and inference in one process. `./start.sh --mode mock` uses it.
- [`search_profile_mock/`](search_profile_mock/README.md) (:7778): the stock search profile's routes plus incident search, run on its own.

What each one serves: [`../.docs/architecture.md`](../.docs/architecture.md#level-1-containers).
