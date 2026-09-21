import argparse

from .pipeline import deduplicate_frames, extract_frames, handoff_to_cvat, validate_input


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="sportif-ml-worker")
    subcommands = command_parser.add_subparsers(dest="command", required=True)

    for command in ("validate-input", "extract-frames", "deduplicate", "handoff-cvat"):
        subcommand = subcommands.add_parser(command)
        subcommand.add_argument("--video-key", required=True)
        subcommand.add_argument("--provenance-key", required=True)

    return command_parser


def main() -> None:
    arguments = parser().parse_args()
    if arguments.command == "validate-input":
        validate_input(arguments.video_key, arguments.provenance_key)
    elif arguments.command == "extract-frames":
        extract_frames(arguments.video_key, arguments.provenance_key)
    elif arguments.command == "deduplicate":
        deduplicate_frames(arguments.video_key, arguments.provenance_key)
    else:
        handoff_to_cvat(arguments.video_key, arguments.provenance_key)


if __name__ == "__main__":
    main()
