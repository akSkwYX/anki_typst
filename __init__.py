from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import struct
import subprocess
import sys

from aqt import gui_hooks, mw

ADDON = __name__.split(".")[0]

DEFAULT_PREAMBLE = (
    "#set page(width: auto, height: auto, margin: 3pt, fill: none)\n"
    "#set text(size: 14pt)\n"
)

FLAGS = re.DOTALL | re.IGNORECASE

TAGS = [
    (re.compile(r"\[typst\](.+?)\[typst\]", FLAGS), lambda s: s),
    (re.compile(r"\[typst$\](.+?)\[typst$\]", FLAGS), lambda s: f"${s.strip()}$"),
    (re.compile(r"\[typst$$\](.+?)\[typst$$\]", FLAGS), lambda s: f"$ {s.strip()} $"),
]

NIGHT_CSS = (
    "<style>.nightMode img.typst,.nightmode img_typst{filter:invert(1);}"
    "img.typst{vertical-align:middle;}</style>"
)

def _config() -> dict:
    return mw.addonManager.getConfig(ADDON) or {}

def _find_typst(cfg: dict) -> str | None:
    path = cfg.get("typst_path") or shutil.which("typst")
    if path:
        return path
    for candidate in (
        "/opt/homebrew/bin/typst",
        "/usr/local/bin/typst",
        os.path.expanduser("~/.cargo/bin/typst"),
        os.path.expanduser("~/.local/bin/typst")
    ):
        if os.path.exists(candidate):
            return candidate
    return None

def _clean(src: str) -> str:
    src = re.sub(r"<br\s*/?>", "\n", src, flags=re.I)
    src = re.sub(r"</?(div|p)[^>]*>", "\n", src, flags=re.I)
    src = re.sub(r"<[^>]+>", "", src)
    src = html.unescape(src).replace("\xa0", " ")
    return src.strip("\n")

def _png_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    return struct.unpack(">II", data[16:24])

def _img_tag(name: str, size: tuple[int, int] | None, ppi: int) -> str:
    dims = ""
    if size:
        w = max(1, round(size[0] * 96 / ppi))
        h = max(1, round(size[1] * 96 / ppi))
        dims = f' width="{w}" height="{h}"'
    return f'<img src="{name}" class="typst"{dims}>'

def _error_box(message: str) -> str:
    return (
        '<pre class="typst error" style="color:#c00;text-align:left;'
        f'white-space:pre-warp">{html.escape(message)}</pre>'
    )

def _render(source: str, preamble: str, postamble: str, cfg: dict) -> str:
    ppi = int(cfg.get("ppi", 192))
    full = preamble + source + postamble
    digest = hashlib.sha1(f"{ppi}\n{full}".encode("utf-8")).hexdigest()
    name = f"typst-{digest}.png"
    media_path = os.path.join(mw.col.media.dir(), name)

    if os.path.exists(media_path):
        with open(media_path, "rb") as f:
            return _img_tag(name, _png_size(f.read(24)), ppi)

    typst = _find_typst(cfg)
    if not typst:
        return _error_box(
            "Typst Cards: `typst` binary not found. Install it or set "
            "'typst_path' in the add-on config."
        )

    cmd = [typst, "compile", "--format", "png", "--ppi", str(ppi)]
    for font_path in cfg.get("font_paths", []):
        cmd += ["--font-path", font_path]
    cmd += ["-", "-"]

    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.run(
            cmd,
            input=full.encode("utf-8"),
            capture_output=True,
            timeout=float(cfg.get("timeout_seconds", 20)),
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return _error_box("Typst Cards: compilation timed out.")
    except OSError as e:
        return _error_box(f"Typst Cards: could not run typst: {e}")

    if proc.returncode != 0 or not proc.stdout:
        return _error_box(proc.stderr.decode("utf-8", errors="replace"))

    mw.col.media.write_data(name, proc.stdout)
    return _img_tag(name, _png_size(proc.stdout), ppi)

def _wrappers_for(card, cfg: dict) -> tuple[str, str]:
    try:
        notetype = card.note_type()
    except AttributeError:
        notetype = card.model()

    pre = notetype.get("latexPre", "")
    post = notetype.get("latexPost", "")
    return pre.rstrip("\n") + "\n", ("\n" + post.strip("\n") + "\n" if post.strip() else "")


def _on_card_will_show(text: str, card, kind: str) -> str:
    if "[typst" not in text.lower() or mw.col is None:
        return text
    
    cfg = _config()
    preamble, postamble = _wrappers_for(card, cfg)
    changed = False

    for pattern, wrap in TAGS:
        def repl(match: re.Match) -> str:
            nonlocal changed
            changed = True
            return _render(wrap(_clean(match.group(1))), preamble, postamble, cfg)
        
        text = pattern.sub(repl, text)

    if changed and (cfg.get("invert_in_night_mode", True) or cfg.get("always_invert", False)):
        text += NIGHT_CSS
    return text

gui_hooks.card_will_show.append(_on_card_will_show)
