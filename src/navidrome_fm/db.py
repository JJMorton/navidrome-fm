from __future__ import annotations

from enum import Enum
from functools import cached_property
from pathlib import Path
import re
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterator

from . import api
from .log import Log


@dataclass(frozen=True)
class LastFMTrackEntry:
    """A track in the last.fm database"""

    id: str
    title: str
    artist: str
    album: str | None
    mbid: str | None

    def __str__(self) -> str:
        return f"{self.artist} / {self.album} -- {self.title}"


@dataclass(frozen=True)
class NavidromeTrackEntry:
    """A track in the navidrome database"""

    id: str
    title: str
    artist: str
    album: str | None
    mbz_recording_id: str | None

    def __str__(self) -> str:
        return f"{self.artist} / {self.album} -- {self.title}"


@dataclass(frozen=True)
class ScrobbleEntry:
    """A scrobble in the database"""

    track_id: str
    timestamp: int


@dataclass(frozen=True)
class ScrobbleDB:
    """Database containing tracks and scrobbles of these tracks"""

    con: sqlite3.Connection
    log: Log

    def __post_init__(self):
        cur = self.con.cursor()
        cur.execute("PRAGMA foreign_keys = ON")

        # Tracks are uniquely defined by their unique hash, affected by all the
        # other columns as defined by TrackModel.track_id
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS track (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT,
                mbid TEXT
            )
            """
        )

        # Scrobbles are uniquely identified by the combination of a track and
        # a timestamp.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS scrobble (
                timestamp INTEGER NOT NULL,
                trackid TEXT NOT NULL,
                FOREIGN KEY(trackid) REFERENCES track(id),
                PRIMARY KEY(trackid, timestamp)
            )
            """
        )

        # Link each track to a navidrome ID if possible
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS match (
                trackid TEXT NOT NULL,
                navidromeid TEXT NOT NULL,
                FOREIGN KEY(trackid) REFERENCES track(id),
                PRIMARY KEY(trackid, navidromeid)
            )
            """
        )

        # List of blacklisted matches, which the user explicitly said are not correct
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                trackid TEXT NOT NULL,
                navidromeid TEXT NOT NULL,
                FOREIGN KEY(trackid) REFERENCES track(id),
                PRIMARY KEY(trackid, navidromeid)
            )
            """
        )

        self.log.info(self, "Initialised scrobble database")

    def add_scrobble_from_api(
        self,
        s: api.ScrobbleModel,
    ) -> bool:
        """
        Save a scrobble from the last.fm API.
        Returns True if this scrobble is new to the database.
        """

        cur = self.con.cursor()

        # Add the track if it doesn't exist
        cur.execute(
            """
            INSERT OR IGNORE INTO track(id, title, artist, album, mbid)
            VALUES(?, ?, ?, ?, ?)
            RETURNING id
            """,
            (s.track_id, s.name, s.artist.name, s.album.name or None, s.mbid or None),
        )
        if len(cur.fetchall()) > 0:
            self.log.info(self, "Added new track")

        # Only add the scrobble if it doesn't exist
        cur.execute(
            """
            INSERT OR IGNORE INTO scrobble(timestamp, trackid)
            VALUES(?, ?)
            RETURNING trackid
            """,
            (s.date.uts, s.track_id),
        )
        new = len(cur.fetchall()) > 0
        if new:
            self.log.info(self, "New scrobble recorded")

        self.con.commit()
        return new

    def iter_tracks(self) -> Iterator[LastFMTrackEntry]:
        """Iterate all saved tracks"""
        cur = self.con.cursor()
        cur.execute("SELECT id, title, artist, album, mbid FROM track")
        for id, title, artist, album, mbid in cur:
            yield LastFMTrackEntry(id, title, artist, album, mbid)
        cur.close()

    def count_scrobbles(self) -> int:
        """Number of scrobbles saved"""
        cur = self.con.cursor()
        res = cur.execute("SELECT COUNT(*) from scrobble").fetchall()
        assert len(res) == 1 and len(res[0]) == 1
        return res[0][0]

    def count_tracks(self) -> int:
        """Number of tracks saved"""
        cur = self.con.cursor()
        res = cur.execute("SELECT COUNT(*) from track").fetchall()
        assert len(res) == 1 and len(res[0]) == 1
        return res[0][0]

    def count_matched(self) -> int:
        """Number of matched last.fm <--> navidrome tracks"""
        cur = self.con.cursor()
        res = cur.execute("SELECT COUNT(*) from match").fetchall()
        assert len(res) == 1 and len(res[0]) == 1
        return res[0][0]


class MatchStatus(Enum):
    MATCH = 1
    NO_MATCH = 2
    CHOICE_REQUIRED = 3


@dataclass(frozen=True)
class NavidromeUser:
    id: str
    user_name: str


@dataclass(frozen=True)
class NavidromeScrobbleMatcher:
    scrobbles: ScrobbleDB
    navidrome_db: Path
    log: Log

    @cached_property
    def _con(self) -> sqlite3.Connection:
        assert sqlite3.SQLITE_ATTACH
        cur = self.scrobbles.con.cursor()
        # FIXME: Don't want to accidentally attach twice! Check that we haven't already.
        cur.execute("ATTACH ? AS db_navidrome", (self.navidrome_db.as_posix(),))
        return self.scrobbles.con

    def get_user_or_only(self, user_id: str | None) -> NavidromeUser | None:
        """
        Get the Navidrome user with `user_id`, or if there's only one user,
        return that one.
        """
        cur = self._con.cursor()
        cur.execute("SELECT id, user_name FROM user")
        users = [NavidromeUser(*u) for u in cur.fetchall()]
        if user_id is not None:
            users = [u for u in users if u.user_name == user_id]

        if len(users) > 1:
            self.log.bad(self, f"Multiple navidrome users found, I don't know which to use")
            return None

        if len(users) == 0:
            self.log.bad(self, f"No users found in the Navidrome database")
            return None

        return users[0]

    def save_match(self, t1: NavidromeTrackEntry, t2: LastFMTrackEntry):
        cur = self.scrobbles.con.cursor()
        cur.execute(
            """
            INSERT INTO match(trackid, navidromeid)
            VALUES(?, ?)
            RETURNING trackid
            """,
            (t2.id, t1.id),
        )
        if not cur.fetchall():
            self.log.bad(self, f"Failed to save match for {t1}")
        self.scrobbles.con.commit()

    def blacklist_match(self, t1: NavidromeTrackEntry, t2: LastFMTrackEntry):
        cur = self.scrobbles.con.cursor()
        cur.execute(
            """
            INSERT INTO blacklist(trackid, navidromeid)
            VALUES(?, ?)
            RETURNING trackid
            """,
            (t2.id, t1.id),
        )
        if not cur.fetchall():
            self.log.bad(self, f"Failed to save blacklisted match for {t1}")
        self.scrobbles.con.commit()

    def is_blacklisted(self, t1: NavidromeTrackEntry, t2: LastFMTrackEntry) -> bool:
        cur = self.scrobbles.con.cursor()
        cur.execute(
            """
            SELECT trackid FROM blacklist
            WHERE trackid=? AND navidromeid=?
            """,
            (t2.id, t1.id),
        )
        return len(cur.fetchall()) > 0

    def iter_unmatched(self) -> Iterator[NavidromeTrackEntry]:
        cur = self._con.cursor()
        # Find all navidrome tracks that aren't matched to any last.fm tracks
        for id, title, artist, album, mbz_recording_id in cur.execute(
            """
            SELECT id, title, artist, album, mbz_recording_id
            FROM db_navidrome.media_file t1
            LEFT JOIN match t2 ON t2.navidromeid = t1.id
            WHERE t2.trackid IS NULL
            """
        ):
            yield NavidromeTrackEntry(id, title, artist, album, mbz_recording_id)

    def update_playcounts(self, user_id: str):

        # TODO: Album and artist play counts are also stored in the `annotation` table, update these too?

        cur = self._con.cursor()

        cur.execute("begin")

        cur.execute(
            """
            INSERT INTO db_navidrome.annotation(user_id, item_id, item_type, play_count, play_date)
            SELECT
                ? AS user_id,
                counted.id AS item_id,
                "media_file" AS item_type,
                counted.play_count AS play_count,
                datetime(counted.timestamp, 'unixepoch') AS play_date
            FROM (
                SELECT match.navidromeid AS id, MAX(scrobble.timestamp) AS timestamp, COUNT(*) AS play_count
                FROM scrobble
                JOIN match ON scrobble.trackid = match.trackid
                GROUP BY match.navidromeid
            ) AS counted
            WHERE true
            ON CONFLICT(user_id, item_id, item_type) DO UPDATE
            SET play_count = excluded.play_count, play_date = excluded.play_date
            WHERE db_navidrome.annotation.play_count < excluded.play_count
            OR unixepoch(db_navidrome.annotation.play_date) < unixepoch(excluded.play_date)
            RETURNING item_id, play_count, play_date
            """,
            (user_id,)
        )
        changes = cur.fetchall()

        if len(changes) == 0:
            self.log.good(self, "All tracks are up to date.")
            return

        # # FIXME: remove this debugging code
        # with open("changes.csv", "w") as f:
        #     print("id,play_count,play_date", file=f)
        #     for c in changes:
        #         print(f"{c[0]},{c[1]},{c[2]}", file=f)

        if input(f"Updating play counts and dates for {len(changes)} tracks, OK? [Y/N] ").lower() == "y":
            cur.execute("commit")
            self.log.good(self, "Successfully updated!")
        else:
            cur.execute("rollback")
            self.log.info(self, "Aborted changes")

    def match_lastfm_tracks_for(
        self,
        track: NavidromeTrackEntry,
        interactive: bool,
        fuzzy: bool,
        min_ratio_all: float = 0.9,
        min_ratio_each: float = 0.7,
        min_overlap: float = 5,
    ) -> tuple[MatchStatus, list[LastFMTrackEntry]]:
        """Find all the last.fm tracks which match the given navidrome track. Returns their IDs."""

        cur = self._con.cursor()

        # First try MusicBrainz ID, always correct
        matches = cur.execute(
            """
            SELECT id, title, artist, album, mbid FROM track
            WHERE mbid=?
            """,
            (track.mbz_recording_id,),
        ).fetchall()
        if len(matches) > 0:
            return MatchStatus.MATCH, [LastFMTrackEntry(*m) for m in matches]

        # Next try exact artist, album, and title match
        matches = cur.execute(
            """
            SELECT id, title, artist, album, mbid FROM track
            WHERE title=? AND artist=? AND album=?
            COLLATE NOCASE
            """,
            (track.title, track.artist, track.album),
        ).fetchall()
        if len(matches) > 0:
            return MatchStatus.MATCH, [LastFMTrackEntry(*m) for m in matches]

        if not fuzzy:
            return MatchStatus.NO_MATCH, []

        # Now try fuzzy matching
        # TODO: Move fuzzy matching to separate file
        # TODO: Make a small TUI for the manual match checking? Type numbers to check/uncheck corresponding matches.

        def trim_feature(t: str) -> str:
            """Remove everything after either 'feat.' or 'ft.'"""
            res = re.sub(r"^(.*)feat\. .*$", lambda m: m.group(1), t)
            res = re.sub(r"^(.*)ft\. .*$", lambda m: m.group(1), res)
            return res

        def matcher_for(
            t1: NavidromeTrackEntry, t2: LastFMTrackEntry
        ) -> SequenceMatcher:
            return SequenceMatcher(
                None,
                (
                    trim_feature(t1.title) + trim_feature(t1.artist) + (t1.album or "")
                ).lower(),
                (
                    trim_feature(t2.title) + trim_feature(t2.artist) + (t2.album or "")
                ).lower(),
            )

        def min_match_ratio(t1: NavidromeTrackEntry, t2: LastFMTrackEntry) -> float:
            pairs = [
                (trim_feature(t1.title), trim_feature(t2.title)),
                (trim_feature(t1.artist), trim_feature(t2.artist)),
                (t1.album or "", t2.album or ""),
            ]
            return min(
                SequenceMatcher(None, a.lower(), b.lower()).ratio() for a, b in pairs
            )

        def enough_overlap(t1: NavidromeTrackEntry, t2: LastFMTrackEntry) -> bool:
            pairs = [
                (trim_feature(t1.title), trim_feature(t2.title)),
                (trim_feature(t1.artist), trim_feature(t2.artist)),
                (t1.album or "", t2.album or ""),
            ]
            return all(
                SequenceMatcher(None, a.lower(), b.lower()).find_longest_match().size
                >= min(len(a), len(b), min_overlap)
                for a, b in pairs
            )

        # Match by title OR artist OR album
        matches = [
            LastFMTrackEntry(id, title, artist, album or None, mbid=None)
            for id, title, artist, album in cur.execute(
                """
                SELECT id, title, artist, album FROM track
                WHERE title=? OR artist=? OR album=?
                """,
                (track.title, track.artist, track.album),
            ).fetchall()
        ]

        # Compute match ratios for each
        matchers = [matcher_for(track, match) for match in matches]
        ratios = [m.ratio() for m in matchers]
        match_ratio_sort = sorted(
            zip(matches, ratios), key=lambda z: z[1], reverse=True
        )

        # Filter by matches that have above `min_ratio` for EVERY field
        match_ratio_sort = [
            (match, ratio)
            for match, ratio in match_ratio_sort
            if min_match_ratio(track, match) > min_ratio_each
            and enough_overlap(track, match)
            and not self.is_blacklisted(track, match)
        ]

        if match_ratio_sort:
            # Accept if above `accept_ratio`
            acceptable = [
                (match, ratio)
                for match, ratio in match_ratio_sort
                if ratio > min_ratio_all
            ]
            if acceptable:
                return MatchStatus.MATCH, [t for t, _ in acceptable]

            # Ask user otherwise
            if interactive:
                print(f"Candidates for {track}:")
                for i, (match, ratio) in enumerate(match_ratio_sort):
                    print(f"    [{i + 1:2}] ({ratio * 100:2.0f}%) {match}")
                print("    [ 0] Reject all")
                print("    [  ] Skip")
                inp = input(f"    [0-{len(match_ratio_sort)}] > ")
                if inp == "":
                    return MatchStatus.CHOICE_REQUIRED, []
                choices = [int(v) for v in inp.split(",")]
                if any(c > 0 for c in choices) and all(
                    c <= len(match_ratio_sort) for c in choices
                ):
                    return MatchStatus.MATCH, [
                        match_ratio_sort[c - 1][0] for c in choices
                    ]
                else:
                    for match, _ in match_ratio_sort:
                        self.blacklist_match(track, match)

            else:
                return MatchStatus.CHOICE_REQUIRED, []

        # Give up
        return MatchStatus.NO_MATCH, []
