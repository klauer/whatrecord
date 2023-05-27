"""
"whatrecord server" is used to start an aiohttp-backed web server which hosts
startup script and record information.
"""

import argparse
import logging

from ..server import main

logger = logging.getLogger(__name__)
DESCRIPTION = __doc__


def build_arg_parser(parser=None):
    if parser is None:
        parser = argparse.ArgumentParser()

    parser.description = DESCRIPTION
    parser.formatter_class = argparse.RawTextHelpFormatter

    parser.add_argument(
        '--scripts',
        type=str,
        nargs='*',
        help="Startup script filename(s)"
    )

    parser.add_argument(
        '--script-loader',
        type=str,
        nargs='*',
        help="Run an external script to get IOC configuration information",
    )

    archive_group = parser.add_mutually_exclusive_group()

    archive_group.add_argument(
        '--archive-management-url',
        type=str,
        help='Archiver management URL for finding archived PVs.'
    )

    parser.add_argument(
        '--gateway-config',
        type=str,
        help='Gateway configuration file or directory'
    )

    parser.add_argument(
        '--standin-directory',
        type=str,
        nargs='*',
        help='Map a "stand-in" directory to another on disk'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=8898,
        help='Web server TCP port'
    )

    parser.add_argument(
        '--tracemalloc',
        dest='use_tracemalloc',
        action='store_true',
        help='[Debug] Use tracemalloc to debug server memory usage'
    )

    parser.add_argument(
        '--dump-for-offline-usage',
        dest="offline_dump_target",
        type=str,
        help='Dump the server state for offline usage of the whatrecord frontend'
    )

    return parser


__all__ = ["build_arg_parser", "main"]
