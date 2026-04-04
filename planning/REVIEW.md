# Review

Reviewed the changes present in the worktree since `HEAD` (`84dd49f`).

There are no tracked modifications relative to the last commit. The review therefore covers the new untracked planning docs:

- `planning/MASSIVE_API.md`
- `planning/MARKET_INTERFACE.md`
- `planning/MARKET_SIMULATOR.md`

## Findings

1. High: `planning/MASSIVE_API.md:111` contains a broken async example. `fetch_snapshots()` calls `client.get(...)` without `await`, then immediately calls `raise_for_status()` and `json()` on the coroutine object. Anyone copying the primary sample will hit a runtime error instead of receiving data. The example needs `response = await client.get(url, params=params)`.

2. High: `planning/MARKET_INTERFACE.md:145`, `planning/MARKET_INTERFACE.md:148`, `planning/MARKET_INTERFACE.md:193`, and `planning/MARKET_INTERFACE.md:257` describe a Massive provider that never evicts removed tickers from `_cache`. `set_watchlist()` only replaces `_watchlist`, and the SSE sketch emits directly from `provider.get_all_prices()`. After a ticker is removed from the watchlist, it would continue to appear in the cache and keep streaming to clients indefinitely.

3. Medium: `planning/MARKET_INTERFACE.md:94`, `planning/MARKET_INTERFACE.md:302`, and `planning/MARKET_INTERFACE.md:316` contradict the source-of-truth requirements in `planning/PLAN.md:156` and `planning/PLAN.md:525`. The plan says the Massive poller should re-read the current watchlist dynamically each cycle; this design instead snapshots tickers at startup and relies on imperative `provider.set_watchlist(...)` calls from route handlers. That creates a correctness gap for any code path that mutates the DB without also synchronizing the provider.

4. Medium: `planning/MARKET_SIMULATOR.md:383` and `planning/MARKET_SIMULATOR.md:392` show test examples that start the simulator background task and then either never stop it or manually call `sim._tick()` while the background loop is still live. If copied into the real test suite, those examples will leak pending asyncio tasks and can become nondeterministic because `_tick_loop()` may mutate the same state concurrently. The examples should either avoid `start()` in unit tests that drive `_tick()` directly or ensure teardown awaits `sim.stop()`.
