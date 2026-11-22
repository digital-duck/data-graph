"""Running a shipped recipe: data-graf's defaults over GraphLang's `graphlang.recipes` (as app-graph's `appgraph.run`).

    from datagraf.run import run_recipe
    run = run_recipe("ask", {"question": ..., "model": ..., "db": ...}, llm="claude_cli:claude-sonnet-5-5", max_llm_calls=3)
    run.outputs["sql"], run.outputs["status"], run.llm_calls

Recipes are found by name under `datagraf/recipes/<name>/<name>.graph.yaml`; the model defaults to `claude_cli`; run logs go to
~/data-graf/logs. `llm` is a dd-llm `ADAPTER[:MODEL]` string or an object with `generate(prompt, model=None, **opts)`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from graphlang.recipes import Recipe, RecipeError, RecipeRun

__all__ = ["RECIPES", "DEFAULT_LLM", "LOG_DIR", "RecipeError", "RecipeRun", "recipe_path", "run_recipe"]

RECIPES = Path(__file__).parent / "recipes"
DEFAULT_LLM = "claude_cli"
LOG_DIR = Path.home() / "data-graf" / "logs"


def recipe_path(name: str | Path) -> Path:
    p = Path(name)
    if p.suffix == ".yaml":
        return p
    return RECIPES / str(name) / f"{name}.graph.yaml"


def run_recipe(name: str | Path, inputs: dict[str, Any], *, llm: str | Any = DEFAULT_LLM, max_llm_calls: Optional[int] = None,
               max_steps: int = 1000, log_dir: Optional[Path] = LOG_DIR, llm_timeout: Optional[int] = None,
               max_tokens: Optional[int] = None) -> RecipeRun:
    """Run a recipe to its end. Raises RecipeError when it stops early (the call cap, a step that failed, a refused policy);
    a run that ends at an error exit returns with `.exit` set."""
    return Recipe(recipe_path(name)).run(inputs, llm=llm, max_llm_calls=max_llm_calls, max_steps=max_steps, log_dir=log_dir,
                                         llm_options={"timeout": int(llm_timeout)} if llm_timeout else None, max_tokens=max_tokens)
