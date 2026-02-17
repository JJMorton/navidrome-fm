import argparse
import sqlite3
from os import environ
from pathlib import Path
from sys import argv

from dotenv import load_dotenv

from . import api
from .db import NavidromeScrobbleMatcher, MatchStatus, ScrobbleDB
from .log import ConsoleLog, Log


def get_api_key(log: Log) -> str | None:
    # Get API key from .env file
    load_dotenv()
    try:
        return environ["LASTFM_API_KEY"]
    except IndexError:
        log.bad(argv[0], "LASTFM_API_KEY environment variable not defined")
        return None


def command_info(args: argparse.Namespace, log: Log) -> int:
    api_key = get_api_key(log)
    if api_key is None:
        return 1

    with sqlite3.Connection(Path(f"scrobbles_{args.user}.db")) as con:
        scrobbles = ScrobbleDB(con, log)
        m = NavidromeScrobbleMatcher(scrobbles, Path(args.database), log)
        user_info = api.get_info(api_key, user=args.user, log=log)
        scrobble_count = scrobbles.count_scrobbles()
        track_count = scrobbles.count_tracks()
        unmatched_count = sum(1 for _ in m.iter_unmatched())
        print(f"{'User':15s}\t{user_info.name}")
        print(
            f"{'Scrobbles':15s}\t{scrobble_count} (local) / {user_info.playcount} (last.fm)"
        )
        print(
            f"{'Tracks':15s}\t{track_count} (local) / {user_info.track_count} (last.fm)"
        )
        print(
            f"{unmatched_count} unmatched Navidrome tracks"
        )

    return 0


def command_get_scrobbles(args: argparse.Namespace, log: Log) -> int:
    api_key = get_api_key(log)
    if api_key is None:
        return 1

    with sqlite3.Connection(Path(f"scrobbles_{args.user}.db")) as con:
        db = ScrobbleDB(con, log)
        try:
            for s in api.get_recenttracks(
                api_key, user=args.user, log=log, page_start=args.page
            ):
                if not db.add_scrobble_from_api(s) and not args.greedy:
                    log.good(
                        argv[0],
                        "Reached previously saved scrobble, finishing. Use --greedy to continue anyway",
                    )
                    return 0
                print(
                    f"{s.date.as_datetime().isoformat()} {s.artist.name} / {s.album.name} -- {s.name}"
                )

            log.good(argv[0], "Finished iterating all pages")

        except api.LastFMAPIError as err:
            log.bad(err, str(err))
            return 1

    return 0


def command_match(args: argparse.Namespace, log: Log) -> int:
    with sqlite3.Connection(Path(f"scrobbles_{args.user}.db")) as con_scrobbles:
        m = NavidromeScrobbleMatcher(
            ScrobbleDB(con_scrobbles, log), Path(args.database), log
        )
        track_count = 0
        matched_count = 0
        fail_count = 0
        uncertain_count = 0
        for track in m.iter_unmatched():
            track_count += 1
            log.info(argv[0], f"Searching for match for {track}")
            status, matches = m.find_lastfm_tracks_for(track, interactive=args.resolve)
            if status == MatchStatus.NO_MATCH:
                fail_count += 1
                log.bad(argv[0], f"No match found!")
            elif status == MatchStatus.MATCH:
                matched_count += 1
                for match in matches:
                    log.good(argv[0], f"Matched to track {match}")
                    m.save_match(track, match)
            else:
                uncertain_count += 1
                log.info(argv[0], "Uncertain match, run with --resolve")

    log.info(argv[0], f"PROCESSED {track_count} UNMATCHED TRACKS, {matched_count} MATCHED, {uncertain_count} REQUIRE CONFIRMATION, {fail_count} NOT MATCHED.")

    return 0


def command_counts(args: argparse.Namespace, log: Log) -> int:
    raise NotImplementedError()


def main_cli() -> int:

    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--user", required=True, help="last.fm username")
    subparsers = parser.add_subparsers()

    parser_info = subparsers.add_parser(
        "info", help="show statistics of saved scrobbles"
    )
    parser_info.add_argument(
        "--database", type=str, required=True, help="path to the Navidrome database"
    )
    parser_info.set_defaults(func=command_info)

    parser_get = subparsers.add_parser(
        "get-scrobbles", help="fetch and save scrobbles from last.fm"
    )
    parser_get.set_defaults(func=command_get_scrobbles)
    parser_get.add_argument(
        "-p", "--page", type=int, default=1, help="start from this page of results"
    )
    parser_get.add_argument(
        "-g",
        "--greedy",
        action="store_true",
        default=False,
        help="don't stop fetching scrobbles when encountering one which exists",
    )

    parser_match = subparsers.add_parser(
        "match-scrobbles", help="match scrobbles with tracks in Navidrome"
    )
    parser_match.set_defaults(func=command_match)
    parser_match.add_argument(
        "--database", type=str, required=True, help="path to the Navidrome database"
    )
    parser_match.add_argument(
        "--resolve",
        action="store_true",
        default=False,
        help="manually resolve uncertain matches",
    )

    parser_counts = subparsers.add_parser(
        "update-counts", help="update Navidrome play counts with last.fm scrobbles"
    )
    parser_counts.set_defaults(func=command_counts)

    args = parser.parse_args()

    log = ConsoleLog()

    return args.func(args, log)
