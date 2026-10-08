import gzip
import hashlib
import json
import os
import posixpath
import re
import shutil
import time
from pathlib import Path


try:
    Import("env")
except NameError:
    env = None


if env is not None:
    PROJECT_DIR = Path(env.subst("$PROJECT_DIR"))
else:
    script_path = globals().get("__file__")
    PROJECT_DIR = Path(script_path).resolve().parents[1] if script_path else Path(os.getcwd())

SOURCE_DIR = PROJECT_DIR / "extras"
OUTPUT_DIR = PROJECT_DIR / ".pio" / "web_data"
LOCK_FILE = PROJECT_DIR / ".pio" / "web_data.lock"

# The web UI is stored gzip-compressed only; asset URLs get a content hash
# (?v=) so the web server can let browsers cache them indefinitely.
WEB_DIR = "web_interface_data"
ASSET_REF = re.compile(r'(\s(?:href|src)=")((?:css|js|img)/[^"]+)"')
CSS_URL = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""")


def minify_json(text):
    if not text.strip():
        return text
    return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)


def minify_html(text):
    text = re.sub(r"<!--(?!\[if).*?-->", "", text, flags=re.S)
    text = re.sub(r">\s+<", "> <", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip() + "\n"


def minify_css(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([{}:;,>+~])\s*", r"\1", text)
    text = text.replace(";}", "}")
    return text.strip() + "\n"


def compact_js(text):
    # Keep JavaScript semantically safe: trim lines and remove empty lines only.
    # Full JS minification is left to external tools to avoid breaking regexes/templates.
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line) + "\n"


def transform_file(source, target):
    suffix = source.suffix.lower()
    if suffix not in {".html", ".css", ".js", ".json"}:
        shutil.copy2(source, target)
        return

    text = source.read_text(encoding="utf-8-sig")
    try:
        if suffix == ".json":
            output = minify_json(text)
        elif suffix == ".html":
            output = minify_html(text)
        elif suffix == ".css":
            output = minify_css(text)
        else:
            output = compact_js(text)
    except Exception as exc:
        print(f"[web-data] keeping original {source.relative_to(SOURCE_DIR)}: {exc}")
        output = text

    target.write_text(output, encoding="utf-8", newline="\n")


def gzip_web_interface(web_dir):
    # Referenced files first: CSS references images, HTML references all.
    order = {".css": 1, ".html": 2}
    files = sorted(
        (path for path in web_dir.rglob("*") if path.is_file()),
        key=lambda path: (order.get(path.suffix.lower(), 0), path.as_posix()),
    )
    versions = {}

    def versioned(from_rel, ref):
        clean = ref.split("?", 1)[0]
        target = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), clean))
        if target not in versions:
            raise ValueError(f"{from_rel}: unknown asset {ref}")
        return f"{clean}?v={versions[target]}"

    for path in files:
        rel = path.relative_to(web_dir).as_posix()
        data = path.read_bytes()
        suffix = path.suffix.lower()
        if suffix == ".css":
            text = CSS_URL.sub(lambda m: f"url({versioned(rel, m.group(1))})", data.decode("utf-8"))
            data = text.encode("utf-8")
        elif suffix == ".html":
            text = ASSET_REF.sub(lambda m: f'{m.group(1)}{versioned(rel, m.group(2))}"', data.decode("utf-8"))
            data = text.encode("utf-8")
        versions[rel] = hashlib.sha256(data).hexdigest()[:8]
        path.with_name(path.name + ".gz").write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
        path.unlink()


def build_web_data():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = None
    for _ in range(120):
        try:
            lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, str(os.getpid()).encode("ascii"))
            break
        except FileExistsError:
            time.sleep(0.25)
    if lock_fd is None:
        raise TimeoutError(f"Timed out waiting for {LOCK_FILE}")

    if OUTPUT_DIR.exists():
        pass

    try:
        if OUTPUT_DIR.exists():
            shutil.rmtree(OUTPUT_DIR)
        OUTPUT_DIR.mkdir(parents=True)

        copied = 0
        source_bytes = 0

        for source in SOURCE_DIR.rglob("*"):
            if source.is_dir():
                continue

            relative = source.relative_to(SOURCE_DIR)
            target = OUTPUT_DIR / relative
            target.parent.mkdir(parents=True, exist_ok=True)

            source_bytes += source.stat().st_size
            transform_file(source, target)
            copied += 1

        if (OUTPUT_DIR / WEB_DIR).is_dir():
            gzip_web_interface(OUTPUT_DIR / WEB_DIR)

        output_bytes = sum(
            path.stat().st_size for path in OUTPUT_DIR.rglob("*") if path.is_file()
        )

        saved = source_bytes - output_bytes
        percent = (saved / source_bytes * 100) if source_bytes else 0
        print(
            f"[web-data] generated {OUTPUT_DIR.relative_to(PROJECT_DIR)} "
            f"from {copied} files: {source_bytes} -> {output_bytes} bytes "
            f"({percent:.1f}% smaller)"
        )
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass


build_web_data()

if env is not None:
    env.Replace(PROJECT_DATA_DIR=str(OUTPUT_DIR))
