"""Lane C owns the price tables; every lane builds its TraceSteps here so costs are comparable."""
from .schemas import TraceStep

# usd per 1M tokens as (input, output), keyed by the model name passed to the API.
# Price Nemotron at a paid rate, not the free tier's 0.
TOKEN_PRICES: dict[str, tuple[float, float]] = {
    # https://aistudio.google.com/docs/pricing, paid tier, standard (not batch), read 2026-11-13.
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),  # rises to (1.50, 7.50) on 2027-01-01
    "gemini-3.1-pro-preview": (2.00, 12.00),  # prompts <= 200k tokens; (4.00, 18.00) above
    # https://www.together.ai/models/nemotron-3-5-super-vl, read 2026-11-13. Together's id for the model.
    # NVIDIA's own endpoint (integrate.api.nvidia.com) lists no per-token price, so its id is left unpriced.
    "nvidia/NVIDIA-Nemotron-3.5-Super-VL": (0.15, 0.50),
    # Same model on NVIDIA's sponsored Verda endpoint (containers.datacrunch.io), which has no price of its own:
    # logged at Together's rate above so it is not counted as free.
    "nemotron-3.5-super-vl": (0.15, 0.50),
}

# usd per second, keyed by Modal's gpu= string. https://modal.com/pricing, read 2026-11-13.
# Modal lists "A10", not "A10G"; its gpu= docs (modal.com/docs/guide/gpu) accept "A10" only.
GPU_PRICES: dict[str, float] = {"L40S": 0.000542, "T4": 0.000164, "A10": 0.000306, "L4": 0.000222}


def llm_step(step: str, model: str, in_tokens: int, out_tokens: int, ms: float, cached: bool = False) -> TraceStep:
    """KeyError on an unpriced model is deliberate: a missing price must not be logged as free."""
    price_in, price_out = TOKEN_PRICES[model]
    usd = 0.0 if cached else (in_tokens * price_in + out_tokens * price_out) / 1e6
    return TraceStep(step=step, model=model, in_tokens=in_tokens, out_tokens=out_tokens, usd=usd, ms=ms, cached=cached)


def gpu_step(step: str, gpu: str, seconds: float, cached: bool = False) -> TraceStep:
    usd = 0.0 if cached else seconds * GPU_PRICES[gpu]
    return TraceStep(step=step, model=gpu, gpu_s=seconds, usd=usd, ms=seconds * 1000, cached=cached)
