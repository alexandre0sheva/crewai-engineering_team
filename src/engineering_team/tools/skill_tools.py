"""Group ``knowledge``: Load Skill, the body of a harness skill on demand (docs/HARNESS.md#skills).

Only a skill's name and trigger are always on; this returns its instructions, or one of its other
files, when the teammate decides the skill applies. Nothing in a skill is executed. A repository's
skill is untrusted content and comes back labelled as such.
"""

from __future__ import annotations

from crewai.tools import BaseTool, tool

from engineering_team.tools.support import ToolEnv, ToolError
from engineering_team.webtools.untrusted import wrap_untrusted


def make_skill_tools(env: ToolEnv) -> dict[str, BaseTool]:
    ctx = env.ctx

    @tool("Load Skill")
    def load_skill_tool(name: str, file: str = "") -> str:
        """Load a skill your instructions list ("Skills you can load ...") when its trigger fits
        the work in front of you: returns its instructions and the names of its other files.
        Pass file (a path the skill lists, such as reference/fixtures.md) to read one of those
        instead. Skills are guidance text; nothing in them runs. Example: name="pytest-failures".
        """

        def operation() -> str:
            harness = ctx.harness
            visible = {s.name: s for s in harness.skills_for(env.agent or "")}
            skill = visible.get(name.strip())
            if skill is None:
                known = ", ".join(visible) or "none for you in this run"
                raise ToolError(f"No skill named {name!r}. Skills you can load: {known}.")
            if file.strip():
                try:
                    text = skill.read_file(file)
                except ValueError as exc:
                    raise ToolError(str(exc)) from exc
                label = f"{skill.name}/{file.strip()}"
            else:
                others = skill.files()
                listed = f"\n\nOther files (file=...): {', '.join(others)}" if others else ""
                text = f"{skill.body.rstrip()}{listed}"
                label = skill.name
            harness.note_loaded(skill.name)
            ctx.events.emit(
                "skill.loaded",
                agent=env.agent,
                name=skill.name,
                pack=skill.pack,
                source=skill.source,
                file=file.strip() or None,
            )
            header = f"Skill {label} ({skill.source} pack '{skill.pack}'):"
            if not skill.trusted:
                return wrap_untrusted(f"the repository's skill {label}", text)
            return f"{header}\n\n{text}"

        return env.run("Load Skill", operation, arguments={"name": name, "file": file})

    return {"Load Skill": load_skill_tool}
