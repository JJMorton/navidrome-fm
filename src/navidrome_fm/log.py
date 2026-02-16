from abc import ABC, abstractmethod
from dataclasses import dataclass

import termcolor


class Log(ABC):
    @abstractmethod
    def info(self, sender: object | str, msg: str) -> None:
        pass

    @abstractmethod
    def good(self, sender: object | str, msg: str) -> None:
        pass

    @abstractmethod
    def bad(self, sender: object | str, msg: str) -> None:
        pass


@dataclass(frozen=True)
class ConsoleLog(Log):
    def info(self, sender: object | str, msg: str) -> None:
        s = sender if isinstance(sender, str) else sender.__class__.__name__
        termcolor.cprint(f"{s}: {msg}", color="blue")

    def good(self, sender: object | str, msg: str) -> None:
        s = sender if isinstance(sender, str) else sender.__class__.__name__
        termcolor.cprint(f"{s}: {msg}", color="green")

    def bad(self, sender: object | str, msg: str) -> None:
        s = sender if isinstance(sender, str) else sender.__class__.__name__
        termcolor.cprint(f"{s}: {msg}", color="red")


@dataclass(frozen=True)
class NullLog(Log):
    def info(self, sender: object | str, msg: str) -> None:
        pass

    def good(self, sender: object | str, msg: str) -> None:
        pass

    def bad(self, sender: object | str, msg: str) -> None:
        pass


__all__ = ["Log", "ConsoleLog", "NullLog"]

