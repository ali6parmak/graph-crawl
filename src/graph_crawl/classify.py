from urllib.parse import urlsplit
from graph_crawl.schemas.graph import ResourceType

_HTML_EXTENSIONS = frozenset({".html", ".htm", ".xhtml", ".shtml"})
_PDF_EXTENSIONS = frozenset({".pdf"})
_DOC_EXTENSIONS = frozenset({".doc", ".docx", ".odt", ".rtf"})
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".ico"})
_VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".avi", ".mov", ".mkv", ".m4v"})
_AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"})
_ARCHIVE_EXTENSIONS = frozenset({".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".bz2"})


EXTENSION_MAP: tuple[tuple[frozenset[str], ResourceType], ...] = (
    (_HTML_EXTENSIONS, ResourceType.html),
    (_PDF_EXTENSIONS, ResourceType.pdf),
    (_DOC_EXTENSIONS, ResourceType.doc),
    (_IMAGE_EXTENSIONS, ResourceType.image),
    (_VIDEO_EXTENSIONS, ResourceType.video),
    (_AUDIO_EXTENSIONS, ResourceType.audio),
    (_ARCHIVE_EXTENSIONS, ResourceType.archive),
)


def _path_extension(url: str) -> str:
    """Lowercased file extension of the URL path's last segment, or '' if none.

    A trailing-slash path (``/dir/``) and the root (``/``) have no extension.
    """
    path: str = urlsplit(url).path
    last = path.rsplit("/", 1)[-1]
    dot = last.rfind(".")
    if dot == -1:
        return ""
    return last[dot:].lower()


def url_resource_type(url: str) -> ResourceType:
    """Cheap pre-fetch resource type guess from the URL path extension.

    A heuristic: extensionless URLs (modern SPAs, CMS routes like ``/about``)
    return ``unknown``, which the crawler treats as 'potentially HTML' and
    fetches. Known non-HTML extensions become discovery-only leaves. Phase 7
    upgrades this with content-based classification.
    """
    extension = _path_extension(url)
    for extensions, resource_type in EXTENSION_MAP:
        if extension in extensions:
            return resource_type
    return ResourceType.unknown if extension == "" else ResourceType.other


def is_html_url(url: str) -> bool:
    """True if the URL is potentially HTML and should be fetched + parsed.

    HTML extensions and extensionless URLs both qualify. Known non-HTML
    extensions (PDF, image, ...) do not; they are recorded as leaves and not
    fetched in Phase 4.
    """
    return url_resource_type(url) in {ResourceType.html, ResourceType.unknown}


def resource_type_from_content_type(content_type: str | None) -> ResourceType | None:
    """Classify by HTTP Content-Type. Returns None if the type cannot be
    decided from the header (caller keeps the URL-extension guess)."""
    if not content_type:
        return None
    content_type = content_type.lower().split(";", 1)[0].strip()
    if content_type in ("text/html", "application/xhtml+xml"):
        return ResourceType.html
    if content_type == "application/pdf":
        return ResourceType.pdf
    if content_type.startswith("image/"):
        return ResourceType.image
    if content_type.startswith("video/"):
        return ResourceType.video
    if content_type.startswith("audio/"):
        return ResourceType.audio
    if content_type in {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-tar",
        "application/gzip",
        "application/x-gzip",
    }:
        return ResourceType.archive
    if content_type == "application/msword" or "officedocument.wordprocessing" in content_type:
        return ResourceType.doc
    return ResourceType.other


def is_html_content_type(content_type: str | None) -> bool:
    """True if a response Content-Type indicates HTML.

    A missing Content-Type is treated as HTML: the URL was only fetched because
    it looked HTML-ish, so we give the server the benefit of the doubt.
    """
    if not content_type:
        return True
    content_type = content_type.lower().split(";", 1)[0].strip()
    return content_type in ("text/html", "application/xhtml+xml")
