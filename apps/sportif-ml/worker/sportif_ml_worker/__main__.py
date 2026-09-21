import argparse

from .pipeline import deduplicate_frames, extract_frames, validate_input


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(prog="sportif-ml-worker")
    subcommands = command_parser.add_subparsers(dest="command", required=True)

    for command in ("validate-input", "extract-frames", "deduplicate"):
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
    else:
        deduplicate_frames(arguments.video_key, arguments.provenance_key)


if __name__ == "__main__":
    main()
