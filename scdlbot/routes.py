"""Routes and accounts. A route is a way out to the internet: a proxy, or a source address of the server. An account
is a logged-in session (its cookies) that always goes out through one route, and logs in to some sites on every
download (always) and to others only for the downloads that need a login (when_needed). README: Routes has the rules.

They come from ROUTES_FILE (TOML, see deploy/routes.sample.toml; `systemctl reload scdlbot` re-reads it),
or without it, from PROXIES, SOURCE_IPS and COOKIES_FILE. Each site takes its routes in turn (round robin),
and a job makes all its requests through one route. RouteWatch leaves a route out for a site while that site
keeps blocking it; when all routes of a site are left out, all of them are used anyway (fail-open). An account
whose downloads keep failing is left out for a day.
"""

import html
import ipaddress
import itertools
import logging
import re
import time
import tomllib
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scdlbot import observe
from scdlbot.config import Config
from scdlbot.links import SITES
from scdlbot.ytdlp import Cookies, url_for_logs

log = logging.getLogger(__name__)

BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
SITE_NAMES = frozenset(site.name for site in SITES)
DEFAULT = "default"  # [sites] entry for every site not listed
EVERY_SITE = "*"  # an account's always for COOKIES_FILE without a routes file
# Errors that say more about the route than about the link: such a download is tried once more through another route,
# and they count against the route (with timeouts) when RouteWatch decides to leave it out:
RETRY_ERRORS = frozenset({"blocked", "network"})
ROUTE_ERRORS = RETRY_ERRORS | {"timeout"}
# Errors that a logged-in account may not get (Instagram's "empty media response", age and audience limits, stories):
LOGIN_ERRORS = frozenset({"login", "restricted"})
# An account whose last this many downloads that needed a login all failed is probably logged out, blocked or under a
# security check: it's left out for its site for a day, or until the routes file is reloaded with new cookies. Not just a few in a row:
# even a logged-in account can't see private posts of accounts it doesn't follow.
# Besides login errors, a blocked session may look like anything: Instagram's "Please wait a few minutes before you
# try again" (HTTP 401) comes out of yt-dlp as "Failed to parse JSON", code "failed".
LOGIN_STREAK = 8
LOGIN_STREAK_ERRORS = LOGIN_ERRORS | ROUTE_ERRORS | {"failed"}
LOGIN_EXCLUSION = 24 * 3600
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")
PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")


class RoutesError(ValueError):
    """A mistake in the routes configuration."""


def site_label(site: str) -> str:
    """Site name for metrics: unknown sites are "other", to keep the number of series small."""
    return site if site in SITE_NAMES else "other"


@dataclass(frozen=True)
class Route:
    """A way out: a proxy or a source address. As a job's route, it may carry an account's cookies (account); an
    account's own route, for the downloads that need a login, has the account's name and goes out through `via`."""

    name: str
    proxy: str | None = None
    source_address: str | None = None  # 0.0.0.0: any IPv4 address (IPv4 only), "::": IPv6 only, or one exact address
    cookies: Cookies | None = field(default=None, compare=False)
    account: str | None = None  # whose cookies these are
    via: str | None = None  # an account's own route: the route it goes out through

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
        how = [f"proxy {url_for_logs(self.proxy)}" if self.proxy else "direct"]
        if self.source_address:
            how.append(f"from {self.source_address}")
        if self.via:
            return f"{self.name} (account, via {self.via}: {', '.join(how)})"
        if self.account:
            how.append(f"as {self.account}")
        return f"{self.name} ({', '.join(how)})"


@dataclass(frozen=True)
class Account:
    """A logged-in session: its cookies, the one route it goes out through, and the sites it logs in to: on every
    download through that route (always), or only for the downloads that need a login (when_needed)."""

    name: str
    route: str
    cookies: Cookies = field(compare=False)
    always: tuple[str, ...] = ()
    when_needed: tuple[str, ...] = ()

    def describe(self) -> str:
        uses = [f"always {', '.join('every site' if site == EVERY_SITE else site for site in self.always)}" if self.always else ""]
        uses.append(f"when needed {', '.join(self.when_needed)}" if self.when_needed else "")
        return f"{self.name} via {self.route} ({'; '.join(use for use in uses if use)})"


def make_route(name: str, proxy: str | None, source_address: str | None) -> Route:
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
    return Route(name, proxy or None, source_address or None)


def parse(data: dict, cookies_dir: Path) -> tuple[list[Route], dict[str, list[str]], list[Account]]:
    """Routes, the routes of each site, and the accounts, from a parsed routes file."""
    if "login" in data:
        raise RoutesError("[login] is gone (1.1.0): give each login route's cookies an [[accounts]] entry with when_needed, see deploy/routes.sample.toml")
    if unknown := set(data) - {"routes", "sites", "accounts"}:
        raise RoutesError(f"unknown sections: {', '.join(sorted(unknown))} (only [[routes]], [sites] and [[accounts]])")
    routes: list[Route] = []
    for number, entry in enumerate(data.get("routes") or [], start=1):
        if not isinstance(entry, dict):
            raise RoutesError(f"route #{number} must be a [[routes]] table")
        name = _name(entry, f"route #{number}", {route.name for route in routes})
        if "cookies" in entry:
            raise RoutesError(f'route {name}: cookies belong to an [[accounts]] entry now (1.1.0), with route = "{name}" and always = [its sites]')
        if unknown := set(entry) - {"name", "proxy", "source_address"}:
            raise RoutesError(f"route {name}: unknown keys {', '.join(sorted(unknown))} (only name, proxy, source_address)")
        routes.append(make_route(name, entry.get("proxy"), entry.get("source_address")))
    if not routes:
        raise RoutesError("no [[routes]]")
    by_name = {route.name: route for route in routes}
    sites = {}
    for site, value in (data.get("sites") or {}).items():
        _check_site("[sites]", site, with_default=True)
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            raise RoutesError(f"[sites] {site}: must be a list of route names")
        if missing := [item for item in value if item not in by_name]:
            raise RoutesError(f"[sites] {site}: no such route {', '.join(missing)}")
        sites[site] = list(dict.fromkeys(value))
    accounts: list[Account] = []
    for number, entry in enumerate(data.get("accounts") or [], start=1):
        if not isinstance(entry, dict):
            raise RoutesError(f"account #{number} must be an [[accounts]] table")
        name = _name(entry, f"account #{number}", set(by_name) | {account.name for account in accounts})
        if unknown := set(entry) - {"name", "route", "cookies", "always", "when_needed"}:
            raise RoutesError(f"account {name}: unknown keys {', '.join(sorted(unknown))} (only name, route, cookies, always, when_needed)")
        if (route := str(entry.get("route") or "")) not in by_name:
            raise RoutesError(f"account {name}: route = {route!r} isn't one of the [[routes]] ({', '.join(by_name)})")
        if not (cookies := str(entry.get("cookies") or "")):
            raise RoutesError(f"account {name}: no cookies")
        always, when_needed = (_account_sites(entry, key, name) for key in ("always", "when_needed"))
        if not always and not when_needed:
            raise RoutesError(f"account {name}: give it always = [sites], when_needed = [sites], or both")
        if both := sorted(set(always) & set(when_needed)):
            raise RoutesError(f"account {name}: {', '.join(both)} can't be in both always and when_needed")
        for site in always:
            if other := next((account.name for account in accounts if account.route == route and site in account.always), None):
                raise RoutesError(f"accounts {other} and {name} both always log in to {site} through {route}: keep one")
        accounts.append(Account(name, route, Cookies(cookies, cookies_dir / f"cookies.{name}.txt"), always, when_needed))
    return routes, sites, accounts


def _name(entry: dict, what: str, taken: set[str]) -> str:
    name = str(entry.get("name") or "")
    if not NAME.fullmatch(name):
        raise RoutesError(f"{what}: name {name!r} must be up to 32 letters, digits, '-', '_' or '.'")
    if name in taken:
        raise RoutesError(f"{name}: this name is used twice (routes and accounts share names)")
    return name


def _check_site(section: str, site: str, with_default: bool = False) -> None:
    keys = (*SITE_NAMES, DEFAULT) if with_default else tuple(SITE_NAMES)
    if site not in keys and "." not in site:
        hint = next((known for known in keys if known.lower() == site.lower()), None)
        raise RoutesError(f"{section}: unknown site {site!r}, " + (f"did you mean {hint!r}?" if hint else f"use one of {', '.join(sorted(keys))} or a domain like 'mixcloud.com'"))


def _account_sites(entry: dict, key: str, name: str) -> tuple[str, ...]:
    value = entry.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RoutesError(f"account {name}: {key} must be a list of sites")
    for site in value:
        _check_site(f"account {name} {key}", site)
    return tuple(dict.fromkeys(value))


def from_env(cfg: Config) -> tuple[list[Route], dict[str, list[str]], list[Account]]:
    """Without ROUTES_FILE: a route for each proxy and source address (PROXIES, SOURCE_IPS), all with COOKIES_FILE."""
    sources = cfg.source_ips or (None,)
    routes = []
    for proxy, source in itertools.product(cfg.proxies or (None,), sources):
        name = (f"{urlsplit(proxy).hostname}:{urlsplit(proxy).port}" if proxy else "direct") + (f"@{source}" if len(sources) > 1 else "")
        routes.append(Route(name, proxy, source))
    if not cfg.cookies_file:
        return routes, {}, []
    cookies = Cookies(cfg.cookies_file, cfg.db_path.parent / "cookies.txt")
    return routes, {}, [Account(f"COOKIES_FILE@{route.name}", route.name, cookies, always=(EVERY_SITE,)) for route in routes]


def load(cfg: Config) -> tuple[list[Route], dict[str, list[str]], list[Account]]:
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
    """Picks the route for each job, remembers how downloads went through each route and account, and which are left out."""

    def __init__(self, routes: list[Route], sites: dict[str, list[str]], accounts: list[Account] | None = None, window: int = 1800):
        self.window = window
        self.routes: dict[str, Route] = {}
        self.sites: dict[str, list[str]] = {}
        self.accounts: dict[str, Account] = {}
        self.login: dict[str, list[str]] = {}  # site: its when_needed accounts
        self.always: dict[tuple[str, str], Account] = {}  # (site, route): the account that always logs in to it there
        self.exclusions: dict[tuple[str | None, str], float] = {}  # (site, or None for all sites; route or account): until (unix time)
        self.turns: Counter[str] = Counter()  # round robin position of each site
        self.attempts: deque[tuple[float, str, str, str | None]] = deque()  # (time, site, route or account, error code or None)
        self.last_errors: dict[tuple[str, str], str] = {}
        self.login_failures: Counter[tuple[str, str]] = Counter()  # (site, account): failures in a row
        self.update(routes, sites, accounts or [])

    def update(self, routes: list[Route], sites: dict[str, list[str]], accounts: list[Account] | None = None) -> None:
        """New routes and accounts (on reload). Exclusions of routes that still exist are kept."""
        self.routes = {route.name: route for route in routes}
        self.sites = sites
        self.accounts = {account.name: account for account in accounts or []}
        self.login, self.always = {}, {}
        for account in self.accounts.values():
            for site in account.when_needed:
                self.login.setdefault(site, []).append(account.name)
            for site in account.always:
                self.always[(site, account.route)] = account
        # A reload is how new cookies come in, so accounts get another chance:
        self.exclusions = {key: until for key, until in self.exclusions.items() if key[1] in self.routes}
        self.login_failures.clear()
        self.set_gauges()

    def cookies(self) -> list[Cookies]:
        return list({id(account.cookies): account.cookies for account in self.accounts.values()}.values())

    def allowed(self, site: str) -> list[Route]:
        names = self.sites.get(site) or self.sites.get(DEFAULT)
        return [self.routes[name] for name in names] if names else list(self.routes.values())

    def excluded(self, site: str, name: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.exclusions.get((site, name), 0) > now or self.exclusions.get((None, name), 0) > now

    def in_use(self, site: str, now: float | None = None) -> list[Route]:
        allowed = self.allowed(site)
        return [route for route in allowed if not self.excluded(site, route.name, now)] or allowed  # fail-open

    def for_site(self, site: str, route: Route) -> Route:
        """The route as the site's downloads use it: with the cookies of the account that always logs in to the site
        through it, if there is one, else without cookies."""
        if account := self.always.get((site, route.name)) or self.always.get((EVERY_SITE, route.name)):
            return replace(route, cookies=account.cookies, account=account.name)
        return route

    def way(self, name: str) -> Route:
        """An account's own route, for the downloads that need a login: its route with its cookies, under its name."""
        account = self.accounts[name]
        route = self.routes[account.route]
        return replace(route, name=account.name, cookies=account.cookies, account=account.name, via=route.name)

    def pick(self, site: str, avoid: str | None = None) -> Route:
        """The next route for a site, in turn. With avoid: another route than that one, if there is any."""
        candidates = [route for route in self.in_use(site) if route.name != avoid] or [route for route in self.allowed(site) if route.name != avoid]
        if not candidates:
            return self.for_site(site, self.routes.get(avoid or "") or self.allowed(site)[0])
        turn = self.turns[site]
        self.turns[site] += 1
        return self.for_site(site, candidates[turn % len(candidates)])

    def pick_login(self, site: str, avoid: str | None = None) -> Route | None:
        """The next when_needed account of a site, for downloads it gives only to logged-in users; None if it has none."""
        names = [name for name in self.login.get(site, []) if name != avoid]
        candidates = [name for name in names if not self.excluded(site, name)] or names
        if not candidates:
            return None
        turn = self.turns[f"login {site}"]
        self.turns[f"login {site}"] += 1
        return self.way(candidates[turn % len(candidates)])

    def untried(self, site: str, tried: set[str], login: bool = False) -> Route | None:
        """A route of the site (with login, a when_needed account) not in tried, if there is any."""
        if login:
            return next((self.way(name) for name in self.login.get(site, []) if name not in tried), None)
        route = next((route for route in self.in_use(site) if route.name not in tried), None)
        return route and self.for_site(site, route)

    def record(self, site: str, route: str, code: str | None, error: str = "") -> None:
        """How a download through a route (or an account's own route, by its name) went: None if it worked, else its error code."""
        now = time.time()
        self.attempts.append((now, site, route, code))
        while self.attempts and self.attempts[0][0] < now - self.window:
            self.attempts.popleft()
        if code and error:
            self.last_errors[(site, route)] = error
        if route in self.login.get(site, ()):
            if code in LOGIN_STREAK_ERRORS:
                self.login_failures[(site, route)] += 1
            elif code is None:
                self.login_failures[(site, route)] = 0
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
        for site, names in self.login.items():
            for name in names:
                observe.ROUTE_UP.labels(site, name).set(0 if self.excluded(site, name, now) else 1)


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

        for (site, name), failures in sorted(pool.login_failures.items()):
            if failures >= LOGIN_STREAK and not pool.excluded(site, name, now):
                pool.exclusions[(site, name)] = now + LOGIN_EXCLUSION
                pool.login_failures[(site, name)] = 0
                messages.append(self._login_message(site, name, failures, now))

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

    def _login_message(self, site: str, name: str, failures: int, now: float) -> str:
        until = datetime.fromtimestamp(now + LOGIN_EXCLUSION, UTC).strftime("%Y-%m-%d %H:%M UTC")
        text = (
            f"🔑 <b>{html.escape(site)}</b> account <b>{html.escape(name)}</b>: the last {failures} downloads that needed a login "
            f"failed through it too. It's probably logged out, blocked or under a security check: log in again, export new cookies "
            f"and reload (systemctl reload scdlbot). Until then it's left out, at most until {until}."
        )
        if last := self.pool.last_errors.get((site, name)):
            text += f"\nLast error: <code>{html.escape(last[:400])}</code>"
        return text

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
