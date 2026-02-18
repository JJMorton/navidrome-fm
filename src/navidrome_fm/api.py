from __future__ import annotations

from hashlib import sha256
from datetime import datetime
from dataclasses import dataclass
from abc import ABC, abstractmethod
import dataclasses
from enum import Enum
from sys import stderr
import requests
from functools import cached_property

from typing import Any, Iterator
from typing_extensions import Self

from . import log

APIResponse = dict[str, Any]


class APIMethod(Enum):
    RECENT_TRACKS = "user.getrecenttracks"
    INFO = "user.getinfo"

    def __str__(self) -> str:
        return f"{self.value}"


class LastFMAPIRequest:
    """An API request with query parameters"""

    logger: log.Log
    parameters: dict[str, str] | None = None
    base_url: str = "https://ws.audioscrobbler.com/2.0/"

    def __init__(
        self,
        method: APIMethod,
        api_key: str,
        headers: dict[str, str] = {},
        logger: log.Log = log.NullLog(),
        **query_parameters,
    ):
        self.headers = headers
        self.parameters = {
            k: ",".join([str(x) for x in v]) if isinstance(v, list) else str(v)
            for k, v in query_parameters.items()
        }
        self.parameters["method"] = str(method)
        self.parameters["api_key"] = api_key
        self.parameters["format"] = "json"
        self.logger = logger

    @cached_property
    def response(self) -> APIResponse:
        """Fetches from the API and converts to a JSON dict"""
        res = requests.get(self.base_url, params=self.parameters, headers=self.headers)
        if res.status_code == 200:
            self.logger.good(self, f"Response OK from {res.url}")
        else:
            self.logger.bad(self, f"Response NOT OK with code {res.status_code} from {res.url}")
        return res.json()


@dataclass(frozen=True, kw_only=True)
class Model(ABC):
    """(Part of) a response from the API"""

    _attr: dict[str, str] = dataclasses.field(default_factory=dict)
    """Additional attributes of this model"""
    _text: str | None = None
    """Plain text content of this model"""

    @classmethod
    @abstractmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        """
        Decides how each field should be parsed from a provided JSON
        dictionary.
        """
        raise NotImplementedError()

    @classmethod
    def from_response(cls, res: APIResponse) -> Self | LastFMAPIError:
        if cls != LastFMAPIError and "error" in res and "message" in res:
            return LastFMAPIError.from_response(res)

        # Deal with the special @attr and #text fields
        attr = res.pop("@attr") if "@attr" in res else dict()
        text = res.pop("#text") if "#text" in res else None

        # List of fields required by the model, excluding _attr and _text
        fields = [
            f
            for f in dataclasses.fields(cls)
            if f.name != "_attr" and f.name != "_text"
        ]
        keys = [f.name for f in fields]
        required = [
            isinstance(f.default, dataclasses._MISSING_TYPE)
            and isinstance(f.default_factory, dataclasses._MISSING_TYPE)
            for f in fields
        ]

        # Make sure APIResponse has all the keys needed to initialise the model
        if not all(k in res for k, req in zip(keys, required) if req):
            return LastFMAPIError(
                error=-1, message=f"Invalid API response, cannot cast to {cls.__name__}"
            )

        # If any of the fields couldn't be parsed, then return None
        fields = {k: cls.parse_field(k, res[k]) for k in keys if k in res}
        if any(v is None for v in fields.values()):
            return LastFMAPIError(
                error=-1, message=f"Failed to parse one or more fields of response"
            )

        # Create the model with the required keys
        return cls(_attr=attr, _text=text, **fields)


# ==============================================================================
# Generic data Models


@dataclass(frozen=True)
class LastFMAPIError(Model, Exception):
    """An error occurred initialising a Model from an API response"""

    error: int
    message: str

    def __str__(self) -> str:
        return f"{self.message} [{self.error}]"

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        return value


@dataclass(frozen=True)
class TrackModel(Model):
    """A track returned by user.getrecenttracks"""

    mbid: str
    """Musicbrainz ID"""
    name: str
    """Track title"""
    url: str
    """last.fm page for this track"""
    streamable: str
    """Whether track can be streamed on last.fm"""
    artist: ArtistModel
    """Artist of this track"""
    album: AlbumModel
    """Album featuring this track"""
    image: list[ImageModel]
    """Cover art for this track"""

    @property
    def is_now_playing(self) -> bool:
        return "nowplaying" in self._attr and self._attr["nowplaying"] == "true"

    @cached_property
    def track_id(self) -> str:
        """
        A unique identifier for the track. Only the fields `mbid`, `name`,
        `artist.name`, and `album.name` are included in the hash, as the URL,
        streamable status, or images have a possibility to change.
        """
        s = f"{self.mbid}{self.name}{self.artist.name}{self.album.name}"
        return sha256(s.encode()).hexdigest()

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        if key == "artist":
            return ArtistModel.from_response(value)
        elif key == "album":
            return AlbumModel.from_response(value)
        elif key == "image":
            return [ImageModel.from_response(v) for v in value]
        else:
            return value


@dataclass(frozen=True)
class ScrobbleModel(TrackModel):
    """A track with a timestamped scrobble"""

    date: DateModel

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        if key == "date":
            return DateModel.from_response(value)
        else:
            return super().parse_field(key, value)


@dataclass(frozen=True)
class ArtistModel(Model):
    """An artist"""

    mbid: str
    """Musicbrainz ID"""

    @property
    def name(self) -> str:
        assert self._text is not None
        return self._text

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        return value


@dataclass(frozen=True)
class AlbumModel(Model):
    """An album"""

    mbid: str
    """Musicbrainz ID"""

    @property
    def name(self) -> str:
        assert self._text is not None
        return self._text

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        return value


@dataclass(frozen=True)
class ImageModel(Model):
    """A cover art image"""

    size: str
    """Descriptor for size of the image"""

    @property
    def url(self) -> str:
        assert self._text is not None
        return self._text

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        return value


@dataclass(frozen=True)
class DateModel(Model):
    """A date"""

    uts: int
    """POSIX timestamp"""

    def as_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.uts)

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        assert key == "uts"
        return int(value)


@dataclass(frozen=True)
class UserModel(Model):
    """A last.fm user"""

    name: str
    playcount: int
    artist_count: int
    track_count: int
    album_count: int
    image: list[ImageModel]
    url: str

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        if key in ("playcount", "artist_count", "track_count", "album_count"):
            return int(value)
        elif key == "image":
            return [ImageModel.from_response(v) for v in value]
        else:
            return value


# ==============================================================================
# Models for user.getrecenttracks


@dataclass(frozen=True)
class RecentTracksResponseModel(Model):
    """One page response of user.getrecenttracks"""

    recenttracks: _RecentTracksContentModel

    @property
    def track(self) -> list[TrackModel]:
        """The tracks on this page"""
        return self.recenttracks.track

    @property
    def total_pages(self) -> int:
        """The number of pages available"""
        assert "totalPages" in self.recenttracks._attr
        return int(self.recenttracks._attr["totalPages"])

    @property
    def page(self) -> int:
        """The current page"""
        assert "page" in self.recenttracks._attr
        return int(self.recenttracks._attr["page"])

    @property
    def per_page(self) -> int:
        """Maximum number of results per page"""
        assert "perPage" in self.recenttracks._attr
        return int(self.recenttracks._attr["perPage"])

    @property
    def total(self) -> int:
        """Total number of results in all pages"""
        assert "total" in self.recenttracks._attr
        return int(self.recenttracks._attr["total"])

    def __str__(self) -> str:
        return f"[{self.__class__.__name__}] {self.total} results total, page {self.page}/{self.total_pages} with {self.per_page} results per page"

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        assert key == "recenttracks"
        return _RecentTracksContentModel.from_response(value)


@dataclass(frozen=True)
class _RecentTracksContentModel(Model):
    """Content of user.getrecenttracks response"""

    track: list[TrackModel]

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        if key == "track":
            tracks = []
            for v in value:
                # Try constructing a scrobble, otherwise fall back to a track
                # without a timestamp (is the case for a now playing track)
                try:
                    tracks.append(ScrobbleModel.from_response(v))
                except:
                    tracks.append(TrackModel.from_response(v))

            # Make sure we got every track
            assert len(tracks) == len(value)
            return tracks


# ==============================================================================
# Models for user.getinfo


@dataclass(frozen=True)
class UserInfoResponseModel(Model):
    """Content of user.getinfo response"""

    user: UserModel

    @classmethod
    def parse_field(cls, key: str, value: Any) -> Any:
        assert key == "user"
        return UserModel.from_response(value)


# ==============================================================================
# API endpoint wrappers


def get_info(api_key: str, user: str, log: log.Log = log.NullLog()) -> UserModel:
    req = LastFMAPIRequest(APIMethod.INFO, api_key=api_key, logger=log, user=user)
    res = UserInfoResponseModel.from_response(req.response)
    if isinstance(res, LastFMAPIError):
        raise res
    return res.user


def get_recenttracks(
    api_key: str, user: str, page_start: int = 1, log: log.Log = log.NullLog()
) -> Iterator[ScrobbleModel]:
    """Iterate all scrobbles from the most recent"""

    def get_page(page: int) -> RecentTracksResponseModel | LastFMAPIError:
        return RecentTracksResponseModel.from_response(
            LastFMAPIRequest(
                APIMethod.RECENT_TRACKS,
                api_key=api_key,
                logger=log,
                user=user,
                extended=0,
                page=page,
            ).response
        )

    res = get_page(page_start)
    while not isinstance(res, LastFMAPIError):
        for t in res.track:
            if isinstance(t, ScrobbleModel):
                yield t
        if res.page == res.total_pages:
            return
        res = get_page(res.page + 1)

    assert isinstance(res, LastFMAPIError)
    raise res
