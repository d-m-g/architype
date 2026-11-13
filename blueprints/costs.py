"""Lane C owns the price tables; every lane builds its TraceSteps here so costs are comparable."""
from .schemas import TraceStep

# usd per 1M tokens as (input, output), keyed by the model name passed to the API.
# Fill in from the providers' current price pages. Price Nemotron at a paid rate, not the free tier's 0.
TOKEN_PRICES: dict[str, tuple[float, float]] = {}

# usd per second, keyed by Modal GPU type ("L40S", "T4", ...). Fill in from modal.com/pricing.
GPU_PRICES: dict[str, float] = {}


def llm_step(step: str, model: str, in_tokens: int, out_tokens: int, ms: float, cached: bool = False) -> TraceStep:
    """KeyError on an unpriced model is deliberate: a missing price must not be logged as free."""
    price_in, price_out = TOKEN_PRICES[model]
    usd = 0.0 if cached else (in_tokens * price_in + out_tokens * price_out) / 1e6
    return TraceStep(step=step, model=model, in_tokens=in_tokens, out_tokens=out_tokens, usd=usd, ms=ms, cached=cached)


def gpu_step(step: str, gpu: str, seconds: float, cached: bool = False) -> TraceStep:
    usd = 0.0 if cached else seconds * GPU_PRICES[gpu]
    return TraceStep(step=step, model=gpu, gpu_s=seconds, usd=usd, ms=seconds * 1000, cached=cached)
