from __future__ import annotations

from enum import Enum
import sqlite3
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterator

from . import api
from .log import Log


@dataclass(frozen=True)
class TrackEntry:
    """A track in the database"""

    id: str
    title: str
    artist: str
    album: str | None
    mbid: str | None

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

    db: sqlite3.Connection
    log: Log

    def __post_init__(self):
        cur = self.db.cursor()
        cur.execute("PRAGMA foreign_keys = ON")

        # Tracks are uniquely defined by their unique hash, affected by all the
        # other columns as defined by TrackModel.track_id
        cur.execute(
            "CREATE TABLE IF NOT EXISTS track ("
            "id TEXT PRIMARY KEY,"
            "title TEXT NOT NULL,"
            "artist TEXT NOT NULL,"
            "album TEXT,"
            "mbid TEXT"
            ")"
        )

        # Scrobbles are uniquely identified by the combination of a track and
        # a timestamp.
        cur.execute(
            "CREATE TABLE IF NOT EXISTS scrobble ("
            "timestamp INTEGER NOT NULL,"
            "trackid TEXT NOT NULL,"
            "FOREIGN KEY(trackid) REFERENCES track(id),"
            "PRIMARY KEY (trackid, timestamp)"
            ")"
        )

        # Link each track to a navidrome ID
        cur.execute(
            "CREATE TABLE IF NOT EXISTS navidrome ("
            "trackid TEXT PRIMARY KEY,"
            "navidromeid TEXT,"
            "FOREIGN KEY(trackid) REFERENCES track(id)"
            ")"
        )

        self.log.good(self, "Initialised scrobble database")

    def add_scrobble_from_api(
        self,
        s: api.ScrobbleModel,
    ) -> bool:
        """
        Save a scrobble from the last.fm API.
        Returns True if this scrobble is new to the database.
        """

        cur = self.db.cursor()

        # Add the track if it doesn't exist
        cur.execute(
            "INSERT OR IGNORE INTO track(id, title, artist, album, mbid) VALUES(?, ?, ?, ?, ?) RETURNING id",
            (s.track_id, s.name, s.artist.name, s.album.name or None, s.mbid or None),
        )
        if len(cur.fetchall()) > 0:
            self.log.info(self, "Added new track")

        # Only add the scrobble if it doesn't exist
        cur.execute(
            "INSERT OR IGNORE INTO scrobble(timestamp, trackid) VALUES(?, ?) RETURNING trackid",
            (s.date.uts, s.track_id),
        )
        new = len(cur.fetchall()) > 0
        if new:
            self.log.info(self, "New scrobble recorded")

        self.db.commit()
        return new

    def play_count(self, track_id: str) -> int:
        """Play count for the given track ID"""
        cur = self.db.cursor()
        scrobbles = cur.execute(
            "SELECT trackid FROM scrobble WHERE trackid=?", (track_id,)
        ).fetchall()
        if len(scrobbles) == 0:
            self.log.bad(self, f"Track with id {track_id} not found in scrobbles")
        return len(scrobbles)

    def iter_tracks(self) -> Iterator[TrackEntry]:
        """Iterate all saved tracks"""
        cur = self.db.cursor()
        cur.execute("SELECT id, title, artist, album, mbid FROM track")
        for id, title, artist, album, mbid in cur:
            yield TrackEntry(id, title, artist, album, mbid)
        cur.close()

    def count_scrobbles(self) -> int:
        """Number of scrobbles saved"""
        cur = self.db.cursor()
        res = cur.execute("SELECT COUNT(*) from scrobble").fetchall()
        assert len(res) == 1 and len(res[0]) == 1
        return res[0][0]

    def count_tracks(self) -> int:
        """Number of tracks saved"""
        cur = self.db.cursor()
        res = cur.execute("SELECT COUNT(*) from track").fetchall()
        assert len(res) == 1 and len(res[0]) == 1
        return res[0][0]

    def count_matched(self) -> int:
        """Number of matched last.fm <--> navidrome tracks"""
        cur = self.db.cursor()
        res = cur.execute("SELECT COUNT(*) from navidrome").fetchall()
        assert len(res) == 1 and len(res[0]) == 1
        return res[0][0]


class MatchStatus(Enum):
    MATCH = 1
    NO_MATCH = 2
    CHOICE_REQUIRED = 3


@dataclass(frozen=True)
class NavidromeScrobbleMatcher:
    # Navidrome DB:
    #
    # table `media_file`
    #  - `mbz_recording_id` matches `track.mbid` from last.fm API
    #
    # table `annotation`
    #  - `item_type` (media_file, artist, album)
    #  - `play_count`

    db_scrobbles: sqlite3.Connection
    db_navidrome: sqlite3.Connection
    log: Log

    def save_match(self, track: TrackEntry, navidrome_id: str | None):
        cur = self.db_scrobbles.cursor()
        cur.execute(
            "INSERT INTO navidrome(trackid, navidromeid) VALUES(?, ?) RETURNING trackid",
            (track.id, navidrome_id),
        )
        if not cur.fetchall():
            self.log.bad(self, f"Failed to save match for {track}")
        self.db_scrobbles.commit()

    def iter_unmatched(self) -> Iterator[TrackEntry]:
        cur = self.db_scrobbles.cursor()
        for id, title, artist, album, mbid in cur.execute(
            "SELECT id, title, artist, album, mbid"
            " FROM track t1"
            " LEFT JOIN navidrome t2 ON t2.trackid = t1.id"
            " WHERE t2.trackid IS NULL"
        ):
            yield TrackEntry(id, title, artist, album, mbid)

    def find_navidrome_id_for(
        self, track: TrackEntry, interactive: bool, accept_ratio: float = 0.9, min_ratio: float = 0.3
    ) -> tuple[MatchStatus, str | None]:
        cur = self.db_navidrome.cursor()

        # First try MusicBrainz ID, always correct
        matches = cur.execute(
            "SELECT id FROM media_file WHERE mbz_recording_id=?", (track.mbid,)
        ).fetchall()
        if len(matches) > 0:
            return MatchStatus.MATCH, matches[0][0]

        # Next try exact artist, album, and title match
        matches = cur.execute(
            "SELECT id FROM media_file WHERE title=? AND artist=? AND album=? COLLATE NOCASE",
            (track.title, track.artist, track.album),
        ).fetchall()
        if len(matches) > 0:
            return MatchStatus.MATCH, matches[0][0]

        # Finally try fuzzy matching

        def matcher_for(t1: TrackEntry, t2: TrackEntry) -> SequenceMatcher:
            return SequenceMatcher(
                lambda x: x.lower() in " .,;'’",
                (t1.title + t1.artist + (t1.album or "")).lower(),
                (t2.title + t2.artist + (t2.album or "")).lower(),
            )

        def min_match_ratio(t1: TrackEntry, t2: TrackEntry) -> float:
            pairs = [
                (t1.title, t2.title),
                (t1.artist, t2.artist),
                (t1.album or "", t2.album or ""),
            ]
            return min(
                SequenceMatcher(
                    lambda x: x.lower() in " .,;'’", a.lower(), b.lower()
                ).ratio()
                for a, b in pairs
            )

        # Match by title OR artist OR album
        matches = [
            TrackEntry(id, title, artist, album or None, mbid=None)
            for id, title, artist, album in cur.execute(
                "SELECT id, title, artist, album FROM media_file WHERE title=? OR artist=? OR album=?",
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
            if min_match_ratio(track, match) > min_ratio
        ]

        if match_ratio_sort:
            # Accept if above `accept_ratio`
            match, ratio = match_ratio_sort[0]
            if ratio > accept_ratio:
                self.log.good(
                    self, f"Found fuzzy match for {track} ({ratio * 100:.0f}%)"
                )
                return MatchStatus.MATCH, match.id

            # Ask user otherwise
            if interactive:
                self.log.info(self, f"Candidates for {track}:")
                for i, (match, ratio) in enumerate(match_ratio_sort):
                    self.log.info(self, f"[{i + 1:2}] ({ratio * 100:2.0f}%) {match}")
                self.log.info(self, "[0] Reject all")
                choice = int(input(f"[0-{len(match_ratio_sort)}] > "))
                if choice > 0 and choice <= len(match_ratio_sort):
                    return MatchStatus.MATCH, match_ratio_sort[choice - 1][0].id
            else:
                self.log.info(self, f"Uncertain match for {track}, use --resolve to review")
                return MatchStatus.CHOICE_REQUIRED, None

        # Give up
        self.log.bad(self, f"No match found for {track}")
        return MatchStatus.NO_MATCH, None
