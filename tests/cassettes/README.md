# VCR cassette regeneration

Cassettes under this directory are used by `tests/test_integration_ncbi.py`
under `record_mode=none` (cassette-only; zero live HTTP in CI).

## Regenerating a cassette (one-time, requires network)

1. Set `NCBI_API_KEY` env var (recommended; respects 10 req/s rate limit).
2. Temporarily change `vcr_config` in `tests/conftest.py` to
   `record_mode="once"`.
3. Delete the existing cassette file for the test you are regenerating.
4. Run: `python -m pytest tests/test_integration_ncbi.py -v -m integration`
5. The cassette is written back to `tests/cassettes/<test_name>.yaml`.
6. Revert `record_mode` to `"none"` in `tests/conftest.py` and commit the
   cassette + the reverted config together.

Do not commit cassettes with `api_key` values — `filter_query_parameters`
in `vcr_config` strips them, but verify with `grep api_key tests/cassettes/`
before committing.
