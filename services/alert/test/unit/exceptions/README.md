# Exception and retry-policy tests

`test_error_handler.py` pins the behaviour of `ErrorHandler`, the retry policy
applied around VLM calls: how many attempts `with_retry` makes, the backoff it
computes between them, which exception types are retried versus propagated
immediately, and which ones are logged without a stack trace.

## Running

The tests are pure unit tests — no broker, no Elasticsearch, no VLM. `time.sleep`
is patched, so they assert on the computed backoff instead of waiting for it.

```bash
cd services/alert
python -m pytest test/unit/exceptions/ -v
```

## Exception types

The exception hierarchy lives in `src/handlers/exception_handler/vss_exceptions.py`.
The `VSS` prefix is historical: these are the errors raised around VLM calls, not
anything to do with the VSS service, which the Alerts microservice no longer
talks to. All of them derive from `VSSException`.

| Exception | Raised when |
| --- | --- |
| `VSSConnectionError` | The VLM endpoint cannot be reached. |
| `VSSModelError` | A model operation is rejected, for example an unknown model ID. |
| `VSSMediaUploadError` | Media upload fails — missing path, unreadable or unsupported file. |
| `VSSAPIError` | The API call itself fails: HTTP error, timeout, or empty response. |
| `VSSPromptError` | No prompt can be resolved for the alert type. |
| `VSSResponseError` | The response cannot be parsed into a verdict. |
| `VSSRetryExhaustedError` | Every retry attempt was used up. Chained to the last underlying error, so a caller can tell "gave up" apart from "failed once". |

`VSSRetryExhaustedError` is only reached for the types passed in `ErrorHandler`'s
`exceptions` argument. A non-retriable failure such as `VSSMediaUploadError` or
`VSSPromptError` propagates on the first attempt rather than consuming the retry
budget.
