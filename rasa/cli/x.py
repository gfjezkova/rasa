import argparse
import importlib.util
import logging
import signal
import sys
import os
from multiprocessing import get_context
from typing import List, Text, Optional

import ruamel.yaml as yaml

from rasa.cli.utils import get_validated_path
from rasa.cli.arguments import x as arguments

from rasa.constants import (
    DEFAULT_ENDPOINTS_PATH,
    DEFAULT_CREDENTIALS_PATH,
    DEFAULT_LOG_LEVEL,
    ENV_LOG_LEVEL,
)
import rasa.utils.io as io_utils

logger = logging.getLogger(__name__)

DEFAULT_RASA_X_HOST = "http://localhost:5002"
DEFAULT_TRACKER_DB = "tracker.db"


# noinspection PyProtectedMember
def add_subparser(
    subparsers: argparse._SubParsersAction, parents: List[argparse.ArgumentParser]
):
    x_parser_args = {
        "parents": parents,
        "conflict_handler": "resolve",
        "formatter_class": argparse.ArgumentDefaultsHelpFormatter,
    }

    if is_rasa_x_installed():
        # we'll only show the help msg for the command if Rasa X is actually installed
        x_parser_args["help"] = "Starts the Rasa X interface."

    shell_parser = subparsers.add_parser("x", **x_parser_args)
    shell_parser.set_defaults(func=rasa_x)

    arguments.set_x_arguments(shell_parser)


def _rasa_service(
    args: argparse.Namespace, endpoints: "AvailableEndpoints", rasa_x_url=None
):
    """Starts the Rasa application."""
    from rasa.core.run import serve_application
    from rasa.nlu.utils import configure_colored_logging

    configure_colored_logging(args.loglevel)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)

    credentials_path = _prepare_credentials_for_rasa_x(
        args.credentials, rasa_x_url=rasa_x_url
    )

    serve_application(
        endpoints=endpoints,
        port=args.port,
        credentials=credentials_path,
        cors=args.cors,
        auth_token=args.auth_token,
        enable_api=True,
        jwt_secret=args.jwt_secret,
        jwt_method=args.jwt_method,
    )


def _prepare_credentials_for_rasa_x(
    credentials_path: Optional[Text], rasa_x_url=None
) -> Text:
    credentials_path = get_validated_path(
        credentials_path, "credentials", DEFAULT_CREDENTIALS_PATH, True
    )
    if credentials_path:
        credentials = io_utils.read_yaml_file(credentials_path)
    else:
        credentials = {}

    # this makes sure the Rasa X is properly configured no matter what
    if rasa_x_url:
        credentials["rasa"] = {"url": rasa_x_url}
    dumped_credentials = yaml.dump(credentials, default_flow_style=False)
    tmp_credentials = io_utils.create_temporary_file(dumped_credentials, "yml")

    return tmp_credentials


def _overwrite_endpoints_for_local_x(endpoints, rasa_x_token, rasa_x_url):
    from rasa.utils.endpoints import EndpointConfig

    endpoints.model = EndpointConfig(
        "{}/projects/default/models/tags/production".format(rasa_x_url),
        token=rasa_x_token,
        wait_time_between_pulls=2,
    )
    if not endpoints.tracker_store:
        endpoints.tracker_store = EndpointConfig(type="sql", db=DEFAULT_TRACKER_DB)


def start_rasa_for_local_rasa_x(args: argparse.Namespace, rasa_x_token: Text):
    """Starts the Rasa X API with Rasa as a background process."""

    from rasa.core.utils import AvailableEndpoints

    args.endpoints = get_validated_path(
        args.endpoints, "endpoints", DEFAULT_ENDPOINTS_PATH, True
    )

    endpoints = AvailableEndpoints.read_endpoints(args.endpoints)

    rasa_x_url = "{}/api".format(DEFAULT_RASA_X_HOST)
    _overwrite_endpoints_for_local_x(endpoints, rasa_x_token, rasa_x_url)

    vars(args).update(
        dict(
            nlu_model=None,
            cors="*",
            auth_token=args.auth_token,
            enable_api=True,
            endpoints=endpoints,
        )
    )

    ctx = get_context("spawn")
    p = ctx.Process(target=_rasa_service, args=(args, endpoints, rasa_x_url))
    p.start()


def is_rasa_x_installed():
    """Check if Rasa X is installed."""

    # we could also do something like checking if `import rasax` works,
    # the issue with that is that it actually does import the package and this
    # takes some time that we don't want to spend when booting the CLI
    return importlib.util.find_spec("rasax") is not None


def generate_rasa_x_token(length=16):
    """Generate a hexadecimal secret token used to access the Rasa X API.

    A new token is generated on every `rasa x` command.
    """

    from secrets import token_hex

    return token_hex(length)


def _configure_logging(args):
    from rasa.core.utils import configure_file_logging

    args.log_level = args.loglevel or os.environ.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL)
    logging.basicConfig(level=args.log_level)
    configure_file_logging(args.log_level, args.log_file)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("pika").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.ERROR)

    if not args.loglevel == logging.DEBUG:
        logging.getLogger().setLevel(logging.WARNING)
        logging.getLogger("py.warnings").setLevel(logging.ERROR)


def rasa_x(args: argparse.Namespace):
    from rasa.cli.utils import print_success, print_error, signal_handler
    from rasa.core.utils import AvailableEndpoints

    signal.signal(signal.SIGINT, signal_handler)

    _configure_logging(args)

    if args.production:
        print_success("Starting Rasa X in production mode... 🚀")

        args.endpoints = get_validated_path(
            args.endpoints, "endpoints", DEFAULT_ENDPOINTS_PATH, True
        )
        endpoints = AvailableEndpoints.read_endpoints(args.endpoints)
        _rasa_service(args, endpoints)
    else:
        if not is_rasa_x_installed():
            print_error(
                "Rasa X is not installed. The `rasa x` "
                "command requires an installation of Rasa X."
            )
            sys.exit(1)

        # noinspection PyUnresolvedReferences
        from rasax.community import local

        rasa_x_token = generate_rasa_x_token()
        start_rasa_for_local_rasa_x(args, rasa_x_token=rasa_x_token)
        local.main(args, ".", args.data, token=rasa_x_token)
