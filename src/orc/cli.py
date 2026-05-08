import click

from orc import __version__
from orc.cli_commands import ingest as ingest_cmd
from orc.cli_commands import mcp as mcp_cmd
from orc.cli_commands import replay as replay_cmd
from orc.cli_commands import research as research_cmd
from orc.cli_commands import search as search_cmd
from orc.cli_commands import trace as trace_cmd
from orc.cli_commands import verify as verify_cmd
from orc.cli_commands import workspace as workspace_cmd


@click.group()
@click.version_option(__version__, prog_name="orc")
def main() -> None:
    """Orc — headless directive runtime for evidence-grounded research and verification."""


main.add_command(workspace_cmd.workspace)
main.add_command(ingest_cmd.ingest_command)
main.add_command(search_cmd.search_command)
main.add_command(verify_cmd.verify_command)
main.add_command(research_cmd.research_command)
main.add_command(trace_cmd.trace_group)
main.add_command(replay_cmd.replay_command)
main.add_command(mcp_cmd.mcp)


if __name__ == "__main__":
    main()
