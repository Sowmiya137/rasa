import argparse
import logging
import os
import platform
import sys

from rasa_sdk import __version__ as rasa_sdk_version

from rasa import version
from rasa.cli import (
    data,
    export,
    interactive,
    run,
    scaffold,
    shell,
    telemetry,
    test,
    train,
    visualize,
    x,
)
from rasa.cli.arguments.default_arguments import add_logging_options
from rasa.cli.utils import parse_last_positional_argument_as_model_path
from rasa.shared.exceptions import RasaException
from rasa.shared.utils.cli import print_error
import rasa.telemetry
from rasa.utils.common import set_log_and_warnings_filters, set_log_level
import rasa.utils.io
import rasa.utils.tensorflow.environment as tf_env

logger = logging.getLogger(__name__)


def create_argument_parser() -> argparse.ArgumentParser:
    """Parse all the command line arguments for the training script."""

    parser = argparse.ArgumentParser(
        prog="rasa",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Rasa command line interface. Rasa allows you to build "
        "your own conversational assistants 🤖. The 'rasa' command "
        "allows you to easily run most common commands like "
        "creating a new bot, training or evaluating models.",
    )

    parser.add_argument(
        "--version",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print installed Rasa version",
    )

    parent_parser = argparse.ArgumentParser(add_help=False)
    add_logging_options(parent_parser)
    parent_parsers = [parent_parser]

    subparsers = parser.add_subparsers(help="Rasa commands")

    scaffold.add_subparser(subparsers, parents=parent_parsers)
    run.add_subparser(subparsers, parents=parent_parsers)
    shell.add_subparser(subparsers, parents=parent_parsers)
    train.add_subparser(subparsers, parents=parent_parsers)
    interactive.add_subparser(subparsers, parents=parent_parsers)
    telemetry.add_subparser(subparsers, parents=parent_parsers)
    test.add_subparser(subparsers, parents=parent_parsers)
    visualize.add_subparser(subparsers, parents=parent_parsers)
    data.add_subparser(subparsers, parents=parent_parsers)
    export.add_subparser(subparsers, parents=parent_parsers)
    x.add_subparser(subparsers, parents=parent_parsers)

    return parser


def print_version() -> None:
    """Prints version information of rasa tooling and python."""

    python_version, os_info = sys.version.split("\n")
    try:
        from rasax.community.version import __version__

        rasa_x_info = __version__
    except ModuleNotFoundError:
        rasa_x_info = None

    print(f"Rasa Version     : {version.__version__}")
    print(f"Rasa SDK Version : {rasa_sdk_version}")
    print(f"Rasa X Version   : {rasa_x_info}")
    print(f"Python Version   : {python_version}")
    print(f"Operating System : {platform.platform()}")
    print(f"Python Path      : {sys.executable}")


def main() -> None:
    # Running as standalone python application

    parse_last_positional_argument_as_model_path()
    arg_parser = create_argument_parser()
    cmdline_arguments = arg_parser.parse_args()

    log_level = (
        cmdline_arguments.loglevel if hasattr(cmdline_arguments, "loglevel") else None
    )
    set_log_level(log_level)

    tf_env.setup_tf_environment()

    # insert current path in syspath so custom modules are found
    sys.path.insert(1, os.getcwd())

    try:
        if hasattr(cmdline_arguments, "func"):
            rasa.utils.io.configure_colored_logging(log_level)
            set_log_and_warnings_filters()
            rasa.telemetry.initialize_error_reporting()
            cmdline_arguments.func(cmdline_arguments)
        elif hasattr(cmdline_arguments, "version"):
            print_version()
        else:
            # user has not provided a subcommand, let's print the help
            logger.error("No command specified.")
            arg_parser.print_help()
            sys.exit(1)
    except RasaException as e:
        # these are exceptions we expect to happen (e.g. invalid training data format)
        # it doesn't make sense to print a stacktrace for these if we are not in
        # debug mode
        logger.debug("Failed to run CLI command due to an exception.", exc_info=e)
        print_error(f"{e.__class__.__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
