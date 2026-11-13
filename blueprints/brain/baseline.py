"""Lane C. The plain-model baseline: one vision model gets the page image and the question, nothing else.

  uv run --env-file .env --extra brain python -m blueprints.brain.baseline --model gemini-3.8-flash

Same seeded 300-question test sample, scoring and run log as eval.py, so the numbers compare with the pipeline's.
"""
import argparse
import base64
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import eval as harness  # the repo-root eval.py; run this module from the repo root
from .. import pipeline
from ..costs import llm_step
from ..schemas import Answer
from ..scoring import split_of
from .plan import GEMINI_URL

PROVIDERS = {  # model prefix -> (base url, key variable)
    "gemini": (GEMINI_URL, "GEMINI_API_KEY"),
    "nemotron": ("https://containers.datacrunch.io/nemotron-3-5-super-vl-b300/v1", "VERDA_INFERENCE_KEY"),
}
PROMPT = """This is a construction drawing. Answer the question about it, counting exactly what is drawn.
Question: {question}
Reply with JSON only: {{"answer": <a number, or text if the question asks for text>, "unit": "count" | "m2" | "m" | "m3" | "ft2" | null}}"""


def page_png(file: Path, long_side: int = 2048) -> bytes:
    """First page of a PDF or an image, as PNG, scaled so its long side is at most long_side pixels."""
    import pymupdf
    page = pymupdf.open(file)[0]
    scale = long_side / max(page.rect.width, page.rect.height)
    if file.suffix.lower() != ".pdf":
        scale = min(scale, 1.0)  # never upscale a raster
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).tobytes("png")


def ask(model: str, path: str, question: str, think: bool = True) -> Answer:
    import openai
    url, key_name = PROVIDERS[model.split("-")[0]]
    client = openai.OpenAI(api_key=os.environ[key_name], base_url=url, max_retries=4, timeout=180)
    image = base64.b64encode(page_png(pipeline.dataset_file(path))).decode()
    # Nemotron's switch; it otherwise thinks for up to thousands of tokens and can run out before answering.
    extra = {} if think else {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    t0 = time.perf_counter()
    reply = client.chat.completions.create(model=model, max_tokens=8192, **extra, messages=[{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}},
        {"type": "text", "text": PROMPT.format(question=question)}]}])
    step = llm_step("baseline", model, reply.usage.prompt_tokens, reply.usage.completion_tokens, (time.perf_counter() - t0) * 1000)
    text = reply.choices[0].message.content or ""
    found = re.findall(r"\{[^{}]*\}", text)
    try:
        parsed = json.loads(found[-1])
    except (IndexError, json.JSONDecodeError):
        return Answer(tier="llm", trace=[step], error=f"unparsed reply: {text[-200:]!r}")
    value, unit = parsed.get("answer"), parsed.get("unit")
    if isinstance(value, float) and value.is_integer() and unit == "count":
        value = int(value)
    return Answer(answer=value, unit=unit if unit in ("count", "m2", "m", "m3", "ft2") else None, tier="llm", trace=[step])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--model", required=True, help="gemini-3.8-flash, gemini-3.1-pro-preview, nemotron-3.5-super-vl, ...")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--no-think", action="store_true", help="turn Nemotron's reasoning off")
    args = p.parse_args()

    everything = [json.loads(line) for line in (pipeline.DATA_ROOT / "questions.jsonl").open(encoding="utf-8")]
    test = [q for q in everything if split_of(q["path"]) == "test"]
    questions = random.Random(0).sample(test, args.limit)  # the sample eval.py --limit draws

    def one(q):
        try:
            return (q["path"], q["question"]), ask(args.model, q["path"], q["question"], think=not args.no_think)
        except Exception as e:  # one bad call must not stop the run; it shows in the error list
            return (q["path"], q["question"]), Answer(error=f"{type(e).__name__}: {e}"[:300])

    answers = {}
    with ThreadPoolExecutor(args.workers) as pool:
        for key, answer in pool.map(one, questions):
            answers[key] = answer
            if len(answers) % 25 == 0:
                print(f"  {len(answers)}/{len(questions)}", flush=True)
    rows = harness.run(questions, lambda path, question: answers[path, question])
    for row in rows:  # harness.run timed the dict lookup; report the model call's time instead
        row.ms = sum(s.ms for s in answers[row.path, row.question].trace)
    out = Path("runs") / f"{time.strftime('%Y%m%d-%H%M%S')}-plain-{args.model}{'-nothink' if args.no_think else ''}.jsonl"
    out.parent.mkdir(exist_ok=True)
    out.write_text("".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8")
    harness.summarise(rows)
    print(f"\n{len(rows)} rows, ${sum(r.cost_usd for r in rows):.4f} total, written to {out}")


if __name__ == "__main__":
    main()
