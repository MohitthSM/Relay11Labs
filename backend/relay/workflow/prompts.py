"""Versioned prompt files: prompts/<name>.v<N>.md, rendered with string.Template ($var)."""

import re
from dataclasses import dataclass
from pathlib import Path
from string import Template


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    template: str

    def render(self, **values: object) -> str:
        return Template(self.template).substitute(values)


def load_prompt(prompts_dir: str | Path, name: str, version: int | None = None) -> Prompt:
    """Load a specific version, or the highest one if version is None."""
    found = {}
    for path in Path(prompts_dir).glob(f"{name}.v*.md"):
        m = re.fullmatch(rf"{re.escape(name)}\.v(\d+)\.md", path.name)
        if m:
            found[int(m.group(1))] = path
    if not found:
        raise FileNotFoundError(f"no prompt named {name!r} in {prompts_dir}")
    chosen = version if version is not None else max(found)
    if chosen not in found:
        raise FileNotFoundError(f"prompt {name!r} has no version {chosen}")
    return Prompt(name, chosen, found[chosen].read_text(encoding="utf-8"))
