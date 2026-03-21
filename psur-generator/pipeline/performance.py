"""Performance and cost reporting for PSUR generation runs."""
from rich.console import Console
from llm_client import get_active_provider

console = Console()


def print_performance_summary(
    total_elapsed: float,
    gen_elapsed: float,
    token_usage: dict,
):
    """Print the latency & token cost summary at the end of a run.

    Args:
        total_elapsed: Full pipeline wall-clock time in seconds.
        gen_elapsed: Section generation time in seconds.
        token_usage: Dict with input_tokens, output_tokens, api_calls, total_latency_s.
    """
    input_tk = token_usage["input_tokens"]
    output_tk = token_usage["output_tokens"]
    api_calls = token_usage["api_calls"]
    api_latency = token_usage["total_latency_s"]

    # Pricing: Anthropic Claude Sonnet ($3/$15 per 1M); OpenAI GPT-4.1 ($2/$8 per 1M)
    # Ollama is local/free
    provider = get_active_provider()
    if provider == "ollama":
        cost_input = 0.0
        cost_output = 0.0
    elif provider == "openai":
        cost_input = (input_tk / 1_000_000) * 2.0
        cost_output = (output_tk / 1_000_000) * 8.0
    else:
        cost_input = (input_tk / 1_000_000) * 3.0
        cost_output = (output_tk / 1_000_000) * 15.0
    cost_total = cost_input + cost_output

    console.print(f"\n{'─' * 56}")
    console.print(f"  [bold]Performance & Cost Summary[/bold]  ({provider})")
    console.print(f"{'─' * 56}")
    console.print(f"  Full pipeline runtime: {total_elapsed:,.1f}s  ({total_elapsed / 60:,.1f} min)")
    console.print(f"  Section generation:    {gen_elapsed:,.1f}s  ({gen_elapsed / 60:,.1f} min)")
    console.print(f"  API latency:         {api_latency:,.1f}s  ({api_calls} calls)")
    console.print(f"  Input tokens:        {input_tk:,}")
    console.print(f"  Output tokens:       {output_tk:,}")
    console.print(f"  Est. cost:           ${cost_total:,.4f}  (in ${cost_input:,.4f} + out ${cost_output:,.4f})")
    console.print(f"{'─' * 56}")
