from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.model_registry import record_model_latency, report_model_failure


def race_ai_candidates(
    candidates: List[Dict[str, Any]],
    validate_fn: Optional[Callable[[Any], Any]] = None,
    label: str = "AI",
    max_workers: int = 3,
    per_race_timeout: float = 18.0,
    hedge_delay_sec: float = 0.0,
) -> Tuple[Any, Dict[str, Any]]:
    """
    Non-blocking 'First Valid Winner' parallel racer across heterogeneous AI models.
    - When hedge_delay_sec > 0, runs candidate #0 solo for `hedge_delay_sec` first (0 extra RPM if fast),
      then launches backups in parallel if #0 is still pending or failed.
    - Uses shutdown(wait=False, cancel_futures=True) so slow models (e.g. nemotron-ultra) never
      block Python after a faster model wins.
    Returns (validated_result, winner_meta_dict).
    """
    valid_cands = [c for c in candidates if callable(c.get("fn"))]
    if not valid_cands:
        raise RuntimeError(f"No runnable candidates provided for {label}")

    worker_count = max(1, min(int(max_workers), len(valid_cands)))
    pool = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ai_race")
    deadline = time.perf_counter() + max(2.0, float(per_race_timeout))
    in_flight: Dict[Future[Any], Tuple[Dict[str, Any], float]] = {}
    last_error: Optional[Exception] = None

    def _submit_cand(cand: Dict[str, Any]) -> Future[Any]:
        started = time.perf_counter()
        fut = pool.submit(cand["fn"])
        in_flight[fut] = (cand, started)
        return fut

    def _consume_done_future(fut: Future[Any]) -> Optional[Tuple[Any, Dict[str, Any]]]:
        nonlocal last_error
        cand, started = in_flight.pop(fut)
        provider = str(cand.get("provider") or "unknown")
        model = str(cand.get("model") or cand.get("name") or "unknown")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        try:
            raw_res = fut.result()
            final_res = validate_fn(raw_res) if validate_fn is not None else raw_res
            if final_res is None:
                raise ValueError("Empty or invalid candidate result")
            record_model_latency(provider, model, elapsed_ms, task=label)
            print(
                f"[Race:{label}] WINNER {provider}/{model} ({elapsed_ms:.0f}ms)",
                flush=True,
            )
            winner_meta = {
                "provider": provider,
                "model": model,
                "name": str(cand.get("name") or f"{provider} ({model})"),
                "latency_ms": round(elapsed_ms, 1),
            }
            return final_res, winner_meta
        except Exception as exc:
            last_error = exc
            print(
                f"[Race:{label}] {provider}/{model} failed ({elapsed_ms:.0f}ms): {str(exc)[:120]}",
                flush=True,
            )
            report_model_failure(provider, model, exc)
            return None

    try:
        next_idx = 0
        # Hedged lead start: give primary candidate a head start to save backup RPM when fast
        if hedge_delay_sec > 0.0 and len(valid_cands) > 1:
            f0 = _submit_cand(valid_cands[0])
            next_idx = 1
            wait_slice = min(float(hedge_delay_sec), max(0.2, deadline - time.perf_counter()))
            done, _ = wait({f0}, timeout=wait_slice)
            if f0 in done:
                won = _consume_done_future(f0)
                if won is not None:
                    return won

        # Fan out remaining candidates up to max_workers and race for first valid finisher
        while next_idx < len(valid_cands) and len(in_flight) < worker_count:
            _submit_cand(valid_cands[next_idx])
            next_idx += 1

        while in_flight:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                last_error = TimeoutError(f"Race '{label}' timed out after {per_race_timeout:.1f}s")
                break
            done, _ = wait(set(in_flight.keys()), timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                last_error = TimeoutError(f"Race '{label}' timed out after {per_race_timeout:.1f}s")
                break
            for fut in done:
                won = _consume_done_future(fut)
                if won is not None:
                    return won
            while next_idx < len(valid_cands) and len(in_flight) < worker_count:
                _submit_cand(valid_cands[next_idx])
                next_idx += 1

        if in_flight and isinstance(last_error, TimeoutError):
            for cand, _ in in_flight.values():
                provider = str(cand.get("provider") or "unknown")
                model = str(cand.get("model") or cand.get("name") or "unknown")
                report_model_failure(provider, model, str(last_error))

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"All {len(valid_cands)} candidates failed for {label}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
