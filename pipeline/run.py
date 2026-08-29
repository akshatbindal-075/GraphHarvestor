"""
pipeline/run.py
---------------
CLI entry point for GraphHarvestor.

Usage
-----
    # Using Groq (default)
    python -m pipeline.run --urls urls.txt

    # Using OpenRouter with a specific model
    python -m pipeline.run --urls urls.txt --provider openrouter --model mistralai/mistral-7b-instruct

    # Passing URLs directly
    python -m pipeline.run --url https://en.wikipedia.org/wiki/Knowledge_graph

    # Playwright for JS-heavy pages
    python -m pipeline.run --urls urls.txt --playwright
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from utils.logger import get_logger, _configure

app = typer.Typer(
    name="graphharvestor",
    help="Scrape → Extract → Resolve → Graph. Build knowledge graphs from the web.",
    add_completion=False,
)
console = Console()
log = get_logger(__name__)


@app.command()
def main(
    url: Optional[list[str]] = typer.Option(
        None, "--url", "-u", help="One or more URLs to scrape (repeatable)."
    ),
    urls_file: Optional[Path] = typer.Option(
        None, "--urls", help="Path to a text file with one URL per line."
    ),
    provider: str = typer.Option(
        "groq",
        "--provider",
        "-p",
        help="LLM provider to use: 'groq' or 'openrouter'.",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Model name override (provider-specific)."
    ),
    output_dir: Path = typer.Option(
        Path("output"), "--output", "-o", help="Directory for output graph files."
    ),
    stem: str = typer.Option(
        "graph", "--stem", "-s", help="Base filename for output files (no extension)."
    ),
    playwright: bool = typer.Option(
        False, "--playwright", help="Use Playwright for JS-rendered pages."
    ),
    threshold: float = typer.Option(
        85.0, "--threshold", "-t", help="Entity resolution similarity threshold (0–100)."
    ),
    log_level: str = typer.Option(
        "INFO", "--log-level", "-l", help="Logging level (DEBUG, INFO, WARNING, ERROR)."
    ),
) -> None:
    """Run the GraphHarvestor pipeline."""
    # Reconfigure logger with CLI-supplied level
    _configure(log_level.upper())

    # ── Collect URLs ─────────────────────────────────────────────────────────
    all_urls: list[str] = list(url or [])
    if urls_file:
        if not urls_file.exists():
            console.print(f"[red]ERROR:[/red] URLs file not found: {urls_file}")
            raise typer.Exit(1)
        all_urls += [
            line.strip()
            for line in urls_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    if not all_urls:
        console.print("[red]ERROR:[/red] No URLs provided. Use --url or --urls.")
        raise typer.Exit(1)

    # ── Choose LLM client ────────────────────────────────────────────────────
    if provider == "groq":
        from llm.groq_client import GroqClient
        llm_client = GroqClient(default_model=model or "llama-3.3-70b-versatile")
    elif provider == "openrouter":
        from llm.openrouter_client import OpenRouterClient
        llm_client = OpenRouterClient(default_model=model or "mistralai/mistral-7b-instruct")
    else:
        console.print(f"[red]ERROR:[/red] Unknown provider '{provider}'. Choose 'groq' or 'openrouter'.")
        raise typer.Exit(1)

    # ── Banner ───────────────────────────────────────────────────────────────
    console.print(Panel.fit(
        f"[bold cyan]GraphHarvestor[/bold cyan]\n"
        f"Provider: [green]{provider}[/green]  Model: [green]{model or 'default'}[/green]\n"
        f"URLs: [yellow]{len(all_urls)}[/yellow]  Output: [yellow]{output_dir}[/yellow]",
        border_style="cyan",
    ))

    # ── Run ──────────────────────────────────────────────────────────────────
    from pipeline.runner import run_pipeline

    graph = run_pipeline(
        urls=all_urls,
        llm_client=llm_client,
        output_dir=output_dir,
        model=model,
        use_playwright=playwright,
        resolution_threshold=threshold,
        stem=stem,
    )

    console.print(
        f"\n[bold green]Done![/bold green] "
        f"Graph: [cyan]{graph.number_of_nodes()}[/cyan] nodes, "
        f"[cyan]{graph.number_of_edges()}[/cyan] edges\n"
        f"Output: [yellow]{output_dir}/{stem}.graphml[/yellow] + [yellow]{output_dir}/{stem}.jsonld[/yellow]"
    )


if __name__ == "__main__":
    app()
