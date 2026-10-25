"""Routes: the ways downloads go out to the internet, each through a proxy or from a source IP address, with its own cookies.

They come from ROUTES_FILE (TOML, see deploy/routes.sample.toml; `systemctl reload scdlbot` re-reads it),
or without it, from PROXIES, SOURCE_IPS and COOKIES_FILE. Each site takes its routes in turn (round robin),
and a job makes all its requests through one route. RouteWatch leaves a route out for a site while that site
keeps blocking it; when all routes of a site are left out, all of them are used anyway (fail-open).
"""

import html
import ipaddress
import itertools
import logging
import re
import time
import tomllib
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scdlbot import observe
from scdlbot.config import Config
from scdlbot.links import SITES
from scdlbot.ytdlp import Cookies

log = logging.getLogger(__name__)

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
SITE_NAMES = frozenset(site.name for site in SITES)
DEFAULT = "default"  # [sites] entry for every site not listed
# Errors that say more about the route than about the link: such a download is tried once more through another route,
# and they count against the route (with timeouts) when RouteWatch decides to leave it out:
RETRY_ERRORS = frozenset({"blocked", "network"})
ROUTE_ERRORS = RETRY_ERRORS | {"timeout"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")
PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")


class RoutesError(ValueError):
    """A mistake in the routes configuration."""


def site_label(site: str) -> str:
    """Site name for metrics: unknown sites are "other", to keep the number of series small."""
    return site if site in SITE_NAMES else "other"


@dataclass(frozen=True)
class Route:
    name: str
    proxy: str | None = None
    source_address: str | None = None  # 0.0.0.0: any IPv4 address (IPv4 only), "::": IPv6 only, or one exact address
    cookies: Cookies | None = field(default=None, compare=False)

    def ytdlp_args(self, job_dir: Path) -> list[str]:
        args = ["--proxy", self.proxy] if self.proxy else []
        if self.source_address:
            args += ["--source-address", self.source_address]
        return args + (self.cookies.args(job_dir) if self.cookies else [])

    def http_client(self) -> httpx.AsyncClient:
        """For the bot's own requests in a job (Twitter's embed endpoint, tweet photos), through the same route."""
        transport = httpx.AsyncHTTPTransport(proxy=self.proxy, local_address=self.source_address)
        return httpx.AsyncClient(transport=transport, headers={"User-Agent": BROWSER_UA})

    def describe(self) -> str:
        how = [f"proxy {_without_password(self.proxy)}" if self.proxy else "direct"]
        if self.source_address:
            how.append(f"from {self.source_address}")
        if self.cookies:
            how.append("with cookies")
        return f"{self.name} ({', '.join(how)})"


def _without_password(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}{f':{parts.port}' if parts.port else ''}"


def make_route(name: str, proxy: str | None, source_address: str | None, cookies: str, cookies_dir: Path) -> Route:
    if proxy and (urlsplit(proxy).scheme not in PROXY_SCHEMES or not urlsplit(proxy).hostname):
        raise RoutesError(f"route {name}: proxy must look like http://host:port or socks5://host:port")
    if source_address:
        try:
            source = ipaddress.ip_address(source_address)
        except ValueError:
            raise RoutesError(f"route {name}: source_address {source_address!r} is not an IP address") from None
        try:
            proxy_ip = ipaddress.ip_address(urlsplit(proxy).hostname or "") if proxy else None
        except ValueError:
            proxy_ip = None  # a host name
        if proxy_ip and proxy_ip.version != source.version:
            raise RoutesError(f"route {name}: an IPv{source.version} source_address can't reach the IPv{proxy_ip.version} proxy")
    return Route(name, proxy or None, source_address or None, Cookies(cookies, cookies_dir / f"cookies.{name}.txt") if cookies else None)


def parse(data: dict, cookies_dir: Path) -> tuple[list[Route], dict[str, list[str]]]:
    """Routes and the routes of each site, from a parsed routes file."""
    if unknown := set(data) - {"routes", "sites"}:
        raise RoutesError(f"unknown sections: {', '.join(sorted(unknown))} (only [[routes]] and [sites])")
    routes: list[Route] = []
    for number, entry in enumerate(data.get("routes") or [], start=1):
        if not isinstance(entry, dict):
            raise RoutesError(f"route #{number} must be a [[routes]] table")
        if unknown := set(entry) - {"name", "proxy", "source_address", "cookies"}:
            raise RoutesError(f"route #{number}: unknown keys {', '.join(sorted(unknown))} (only name, proxy, source_address, cookies)")
        name = str(entry.get("name") or "")
        if not NAME.fullmatch(name):
            raise RoutesError(f"route #{number}: name {name!r} must be up to 32 letters, digits, '-', '_' or '.'")
        if any(route.name == name for route in routes):
            raise RoutesError(f"route {name}: this name is used twice")
        routes.append(make_route(name, entry.get("proxy"), entry.get("source_address"), str(entry.get("cookies") or ""), cookies_dir))
    if not routes:
        raise RoutesError("no [[routes]]")
    names = {route.name for route in routes}
    sites: dict[str, list[str]] = {}
    for site, value in (data.get("sites") or {}).items():
        if site not in SITE_NAMES and site != DEFAULT and "." not in site:
            hint = next((known for known in (*SITE_NAMES, DEFAULT) if known.lower() == site.lower()), None)
            raise RoutesError(
                f"[sites]: unknown site {site!r}, "
                + (f"did you mean {hint!r}?" if hint else f"use one of {', '.join(sorted(SITE_NAMES))}, a domain like 'mixcloud.com', or {DEFAULT!r}")
            )
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            raise RoutesError(f"[sites] {site}: must be a list of route names")
        if missing := [item for item in value if item not in names]:
            raise RoutesError(f"[sites] {site}: no such route {', '.join(missing)}")
        sites[site] = list(dict.fromkeys(value))
    return routes, sites


def from_env(cfg: Config) -> tuple[list[Route], dict[str, list[str]]]:
    """Without ROUTES_FILE: a route for each proxy and source address (PROXIES, SOURCE_IPS), all with COOKIES_FILE."""
    cookies = Cookies(cfg.cookies_file, cfg.db_path.parent / "cookies.txt") if cfg.cookies_file else None
    sources = cfg.source_ips or (None,)
    routes = []
    for proxy, source in itertools.product(cfg.proxies or (None,), sources):
        name = (f"{urlsplit(proxy).hostname}:{urlsplit(proxy).port}" if proxy else "direct") + (f"@{source}" if len(sources) > 1 else "")
        routes.append(Route(name, proxy, source, cookies))
    return routes, {}


def load(cfg: Config) -> tuple[list[Route], dict[str, list[str]]]:
    if not cfg.routes_file:
        return from_env(cfg)
    try:
        data = tomllib.loads(cfg.routes_file.read_text())
    except OSError as exc:
        raise RoutesError(f"can't read {cfg.routes_file}: {exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise RoutesError(f"{cfg.routes_file}: {exc}") from None
    return parse(data, cfg.db_path.parent)


class RoutePool:
    """Picks the route for each job, remembers how downloads went through each route, and which routes are left out."""

    def __init__(self, routes: list[Route], sites: dict[str, list[str]], window: int = 1800):
        self.window = window
        self.routes: dict[str, Route] = {}
        self.sites: dict[str, list[str]] = {}
        self.exclusions: dict[tuple[str | None, str], float] = {}  # (site, or None for all sites; route): until (unix time)
        self.turns: Counter[str] = Counter()  # round robin position of each site
        self.attempts: deque[tuple[float, str, str, str | None]] = deque()  # (time, site, route, error code or None)
        self.last_errors: dict[tuple[str, str], str] = {}
        self.update(routes, sites)

    def update(self, routes: list[Route], sites: dict[str, list[str]]) -> None:
        """New routes (on reload). Exclusions of routes that still exist are kept."""
        self.routes = {route.name: route for route in routes}
        self.sites = sites
        self.exclusions = {key: until for key, until in self.exclusions.items() if key[1] in self.routes}
        self.set_gauges()

    def cookies(self) -> list[Cookies]:
        return list({id(route.cookies): route.cookies for route in self.routes.values() if route.cookies}.values())

    def allowed(self, site: str) -> list[Route]:
        names = self.sites.get(site) or self.sites.get(DEFAULT)
        return [self.routes[name] for name in names] if names else list(self.routes.values())

    def excluded(self, site: str, name: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.exclusions.get((site, name), 0) > now or self.exclusions.get((None, name), 0) > now

    def in_use(self, site: str, now: float | None = None) -> list[Route]:
        allowed = self.allowed(site)
        return [route for route in allowed if not self.excluded(site, route.name, now)] or allowed  # fail-open

    def pick(self, site: str, avoid: str | None = None) -> Route:
        """The next route for a site, in turn. With avoid: another route than that one, if there is any."""
        candidates = [route for route in self.in_use(site) if route.name != avoid] or [route for route in self.allowed(site) if route.name != avoid]
        if not candidates:
            return self.routes.get(avoid or "") or self.allowed(site)[0]
        turn = self.turns[site]
        self.turns[site] += 1
        return candidates[turn % len(candidates)]

    def record(self, site: str, route: str, code: str | None, error: str = "") -> None:
        """How a download through a route went: None if it worked, else its error code."""
        now = time.time()
        self.attempts.append((now, site, route, code))
        while self.attempts and self.attempts[0][0] < now - self.window:
            self.attempts.popleft()
        if code and error:
            self.last_errors[(site, route)] = error
        observe.ROUTE_ATTEMPTS.labels(site_label(site), route, code or "ok").inc()

    def stats(self, since: float) -> dict[tuple[str, str], Counter]:
        """{(site, route): {error code or None: downloads}} since then."""
        result: dict[tuple[str, str], Counter] = defaultdict(Counter)
        for when, site, route, code in self.attempts:
            if when >= since:
                result[(site, route)][code] += 1
        return result

    def set_gauges(self, now: float | None = None) -> None:
        observe.ROUTE_UP.clear()
        for site in [*sorted(SITE_NAMES | {s for s in self.sites if s != DEFAULT}), "other"]:
            for route in self.allowed(site):
                observe.ROUTE_UP.labels(site, route.name).set(0 if self.excluded(site, route.name, now) else 1)


class RouteWatch:
    """Leaves a route out for a site while the site keeps blocking it (or for all sites, if it can't connect at all),
    and tells the owner. Only when the route fails clearly more than the others: if all fail, it's the site (SiteWatch).
    """

    def __init__(self, pool: RoutePool, min_jobs: int, ratio: float, exclusion: int):
        self.pool, self.min_jobs, self.ratio, self.exclusion = pool, min_jobs, ratio, exclusion
        self.fail_open: set[str] = set()  # sites whose routes are all left out

    def check(self, now: float) -> list[str]:
        """Messages for the owner. Call every few minutes."""
        pool = self.pool
        messages = []
        for (site, name), until in sorted(pool.exclusions.items(), key=str):
            if until <= now:
                del pool.exclusions[(site, name)]
                messages.append(f"↩️ <b>{html.escape(name)}</b> is used again for {html.escape(site) if site else 'all sites'}.")
        stats = pool.stats(now - pool.window)

        # A proxy or network that's down fails every site:
        by_route: dict[str, Counter] = defaultdict(Counter)
        for (_, name), codes in stats.items():
            by_route[name].update(codes)
        for name, codes in sorted(by_route.items()):
            others = [by_route[other] for other in pool.routes if other != name and not pool.excluded("", other, now)]
            if name in pool.routes and not pool.excluded("", name, now) and others and self._worse(codes, others, {"network"}):
                pool.exclusions[(None, name)] = now + self.exclusion
                messages.append(self._message(None, name, codes, {"network"}, now))

        for (site, name), codes in sorted(stats.items()):
            in_use = [route.name for route in pool.allowed(site) if not pool.excluded(site, route.name, now)]
            if name not in in_use or len(in_use) < 2:
                continue  # left out already, or nowhere else to go
            others = [stats.get((site, other), Counter()) for other in in_use if other != name]
            if self._worse(codes, others, ROUTE_ERRORS):
                pool.exclusions[(site, name)] = now + self.exclusion
                messages.append(self._message(site, name, codes, ROUTE_ERRORS, now))

        for site in sorted({site for site, _ in stats} | self.fail_open):
            allowed = pool.allowed(site)
            if all(pool.excluded(site, route.name, now) for route in allowed):
                if site not in self.fail_open:
                    self.fail_open.add(site)
                    messages.append(f"⚠️ All routes of <b>{html.escape(site)}</b> are left out, so all of them are used anyway.")
            else:
                self.fail_open.discard(site)
        pool.set_gauges(now)
        return messages

    def _worse(self, codes: Counter, others: list[Counter], bad_codes: set[str] | frozenset[str]) -> bool:
        total, bad = sum(codes.values()), sum(count for code, count in codes.items() if code in bad_codes)
        if total < self.min_jobs or bad / total < self.ratio:
            return False
        others_total = sum(sum(other.values()) for other in others)
        others_bad = sum(count for other in others for code, count in other.items() if code in bad_codes)
        return not others_total or others_bad / others_total <= bad / total / 2

    def _message(self, site: str | None, name: str, codes: Counter, bad_codes: set[str] | frozenset[str], now: float) -> str:
        total = sum(codes.values())
        bad = {code: count for code, count in codes.most_common() if code in bad_codes}
        reasons = ", ".join(f"{code} {count}" for code, count in bad.items())
        until = datetime.fromtimestamp(now + self.exclusion, UTC).strftime("%H:%M UTC")
        where = f"<b>{html.escape(site)}</b> via <b>{html.escape(name)}</b>" if site else f"<b>{html.escape(name)}</b>, all sites"
        text = (
            f"⛔ {where}: {sum(bad.values())} of {total} downloads failed ({reasons}) in the last {self.pool.window // 60} min, "
            f"so it's left out for {html.escape(site) if site else 'all sites'} until {until}."
        )
        last = self.pool.last_errors.get((site, name)) if site else None
        if last:
            text += f"\nLast error: <code>{html.escape(last[:400])}</code>"
        return text
