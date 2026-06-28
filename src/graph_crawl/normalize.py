from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import idna
import re

# Unreserved characters per RFC 3986, that may be decoded if percent-encoded.
UNRESERVED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443, "ws": 80, "wss": 443}

# Percent-encoded triple matcher, e.g. %7E, %2F, %41
_PCT: re.Pattern[str] = re.compile(r"%([0-9A-Fa-f]{2})")


def _normalize_percent(s: str) -> str:
    def repl(m: re.Match) -> str:
        hex_upper = m.group(1).upper()
        ch = chr(int(hex_upper, 16))
        if ch in UNRESERVED:
            return ch
        return f"%{hex_upper}"

    return _PCT.sub(repl, s)


def _remove_dot_segments(path: str) -> str:
    out: list[str] = []
    input_buf: str = path
    while input_buf:
        # A leading ../ or ./
        if input_buf.startswith("../"):
            input_buf = input_buf[3:]
        elif input_buf.startswith("./"):
            input_buf = input_buf[2:]
        # B current segment
        elif input_buf.startswith("/./"):
            input_buf = "/" + input_buf[3:]
        elif input_buf == "/.":
            input_buf = "/"
        # C: /.. parent segment
        elif input_buf.startswith("/../"):
            input_buf = "/" + input_buf[4:]
            if out:
                out.pop()  # remove last segment
        elif input_buf == "/..":
            input_buf = "/"
            if out:
                out.pop()
        # D: lone . or ..
        elif input_buf == "." or input_buf == "..":
            input_buf = ""
        # E: move one segment to output
        else:
            if input_buf.startswith("/"):
                next_slash = input_buf.find("/", 1)
            else:
                next_slash = input_buf.find("/")
            if next_slash == -1:
                out.append(input_buf)
                input_buf = ""
            else:
                out.append(input_buf[:next_slash])
                input_buf = input_buf[next_slash:]
    return "".join(out)


def _normalize_host(netloc: str) -> tuple[str, str, str]:
    userinfo, _, hostport = netloc.rpartition("@")
    if userinfo:
        userinfo = userinfo + "@"

    # Split host and port.
    # IPv6 literals are wrapped in [] so we have to handle that.
    if hostport.startswith("["):
        # IPv6: [addr]:port or [addr]
        end = hostport.find("]")
        host = hostport[: end + 1]
        rest = hostport[end + 1 :]
        if rest.startswith(":"):
            port = rest[1:]
        else:
            port = ""
    else:
        host, _, port = hostport.partition(":")

    host = host.lower()
    if host and not host.startswith("[") and not host.isdigit():
        # IDN: encode to punycode if it contains non-ASCII.
        try:
            host = idna.encode(host).decode("ascii")
        except idna.IDNAError:
            pass  # leave as-is; we'll record the failure elsewhere

    # Caller passes default_ports so we can compare scheme-specific default.
    # If port equals the scheme default, drop it.
    return userinfo, host, port


def normalize(
    url: str,
    *,
    strip_params: set[str] | None = None,
    default_ports: dict[str, int] | None = None,
) -> str:
    if strip_params is None:
        strip_params = set()
    if default_ports is None:
        default_ports = _DEFAULT_PORTS

    parts = urlsplit(url.strip())

    scheme = parts.scheme.lower()

    userinfo, host, port = _normalize_host(parts.netloc)
    if port:
        try:
            port_int = int(port)
            if default_ports.get(scheme) == port_int:
                port = ""
        except ValueError:
            pass  # malformed port; keep as-is
    netloc = userinfo + host + ((":" + port) if port else "")

    # Path: empty path becomes "/"; percent-encoding normalized; dot-segs removed.
    path = parts.path
    if path == "":
        path = "/"
    path = _normalize_percent(path)
    path = _remove_dot_segments(path)

    # Query: parse (keep blank values, keep duplicates), sort, strip, re-encode.
    query = ""
    if parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if strip_params:
            pairs = [(k, v) for (k, v) in pairs if k not in strip_params]
        pairs.sort()  # by key, then by value — stable
        if pairs:
            # doseq=True handles repeated keys
            query = urlencode(pairs, doseq=True)

    # Fragment: always dropped. It is not part of resource identity.
    fragment = ""

    return urlunsplit((scheme, netloc, path, query, fragment))
