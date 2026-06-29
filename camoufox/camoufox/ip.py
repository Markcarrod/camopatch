import re
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional, Tuple

import requests
from urllib3.exceptions import InsecureRequestWarning

from .exceptions import InvalidIP, InvalidProxy

"""
Helpers to find the user's public IP address for geolocation.
"""


@dataclass
class Proxy:
    """
    Stores proxy information.
    """

    server: str
    username: Optional[str] = None
    password: Optional[str] = None

    @staticmethod
    def parse_server(server: str) -> Tuple[str, str, Optional[str]]:
        """
        Parses the proxy server string.
        """
        proxy_match = re.match(r'^(?:(?P<schema>\w+)://)?(?P<url>.*?)(?:\:(?P<port>\d+))?$', server)
        if not proxy_match:
            raise InvalidProxy(f"Invalid proxy server: {server}")
        return proxy_match['schema'], proxy_match['url'], proxy_match['port']

    def as_string(self) -> str:
        schema, url, port = self.parse_server(self.server)
        if not schema:
            schema = 'http'
        result = f"{schema}://"
        if self.username:
            result += f"{self.username}"
            if self.password:
                result += f":{self.password}"
            result += "@"

        result += url
        if port:
            result += f":{port}"
        return result

    @staticmethod
    def as_requests_proxy(proxy_string: str) -> Dict[str, str]:
        """
        Converts the proxy to a requests proxy dictionary.
        """
        return {
            'http': proxy_string,
            'https': proxy_string,
        }


@lru_cache(128, typed=True)
def valid_ipv4(ip: str) -> bool:
    return bool(re.match(r'^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$', ip))


@lru_cache(128, typed=True)
def valid_ipv6(ip: str) -> bool:
    return bool(re.match(r'^(([0-9a-fA-F]{0,4}:){1,7}[0-9a-fA-F]{0,4})$', ip))


def validate_ip(ip: str) -> None:
    if not valid_ipv4(ip) and not valid_ipv6(ip):
        raise InvalidIP(f"Invalid IP address: {ip}")


@contextmanager
def _suppress_insecure_warning():
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
        yield


@lru_cache(maxsize=None)
def public_ip(proxy: Optional[str] = None) -> str:
    """
    Sends a request to a public IP api.
    Tries each URL in order; returns the first valid IPv4 (or IPv6) found.
    Results are cached so geoip=True never fetches twice in the same process.
    """
    URLS = [
        # --- Strongly IPv4-only endpoints (fastest + most reliable) ---
        "https://api.ipify.org",               # returns bare IP
        "https://ipv4.icanhazip.com",          # Cloudflare-hosted, IPv4 forced
        "https://ip4.seeip.org",               # bare IPv4, JSON-free
        "https://api4.my-ip.io/v2/ip.txt",    # plain text IPv4
        "https://checkip.amazonaws.com",       # AWS, very reliable
        # --- IPv4-preferred (also serve IPv6 on dual-stack hosts) ---
        "https://ipinfo.io/ip",                # ipinfo.io plain text
        "https://ifconfig.me/ip",              # ifconfig.me
        "https://ifconfig.co/ip",              # ifconfig.co
        "https://ipecho.net/plain",            # plain text
        "https://ip.42.pl/raw",                # minimalist
        # --- Generic IPv4 & IPv6 fallbacks ---
        "https://icanhazip.com",               # Cloudflare generic
    ]
    for url in URLS:
        try:
            with _suppress_insecure_warning():
                resp = requests.get(  # nosec
                    url,
                    proxies=Proxy.as_requests_proxy(proxy) if proxy else None,
                    timeout=7,        # raised from 5s — gives slow proxies a chance
                    verify=False,
                    headers={"User-Agent": "curl/8.5.0"},  # plain UA; avoids bot blocks
                )
            resp.raise_for_status()
            ip = resp.text.strip()
            validate_ip(ip)
            return ip
        except requests.exceptions.ProxyError as e:
            raise InvalidProxy(f"Failed to connect to proxy: {proxy}") from e
        except (requests.RequestException, InvalidIP):
            pass
    raise InvalidIP("Failed to get IP address from any provider")
