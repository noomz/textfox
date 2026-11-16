#!/usr/bin/env python3
"""Apply installer font flags after a literal-argv installation callback succeeds."""
import argparse
from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Optional

BEGIN = "/* >>> textfox managed fonts >>> */\n"
END = "/* <<< textfox managed fonts <<< */\n"
MARKER = "textfox managed fonts"
BOM = b"\xef\xbb\xbf"
IDENT = r"-?[A-Za-z_][A-Za-z0-9_-]*"
UNQUOTED = IDENT + r"(?: +" + IDENT + r")*"
FAMILY_NAME = r'''(?:"[^"\\]+"|'[^'\\]+'|''' + UNQUOTED + ")"
FAMILY = re.compile(FAMILY_NAME + r"(?: *, *" + FAMILY_NAME + ")*")
SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(px|pt|em|rem|ex|ch|%)")


def font_value(name, value):
    if not value or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in ";{}!\\/" for c in value):
        raise ValueError("invalid --font-" + name + " value")
    if name == "family":
        if MARKER in value:
            raise ValueError("textfox managed fonts is reserved for the block markers")
        if not FAMILY.fullmatch(value):
            raise ValueError("font family needs quoted names or ASCII identifiers, separated by commas")
        reserved = {"inherit", "initial", "unset", "revert", "revert-layer", "default"}
        for item in re.finditer(FAMILY_NAME, value):
            if item[0][0] not in "\"'" and reserved.intersection(item[0].lower().split()):
                raise ValueError("quote font names containing CSS-wide keywords or default")
    else:
        match = SIZE.fullmatch(value)
        if not match or Decimal(match[1]) <= 0:
            raise ValueError("font size needs a positive decimal with px, pt, em, rem, ex, ch, or %")
    return value


def scan_css(text):
    """Check only lexical safety and at-keywords, not the full CSS grammar."""
    i = 0
    while i < len(text):
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                raise ValueError("unterminated CSS comment")
            i = end + 2
        elif text[i] == "\\":
            i += 2
        elif text[i] in "\"'":
            quote = text[i]
            i += 1
            while i < len(text) and text[i] != quote:
                if text[i] in "\r\n\f":
                    raise ValueError("unterminated CSS string")
                if text[i] == "\\":
                    i += 1
                    if i < len(text) and text[i] == "\r" and text[i:i + 2] == "\r\n":
                        i += 1
                i += 1
            if i >= len(text):
                raise ValueError("unterminated CSS string")
            i += 1
        elif text[i] == "@":
            i += 1
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] in "_-\\" or ord(text[i]) >= 128):
                if text[i] == "\\":
                    raise ValueError("escaped CSS at-keyword names are unsupported")
                i += 1
            if text[start:i].lower() in {"charset", "import", "namespace", "layer"}:
                raise ValueError("@charset, @import, @namespace, and @layer need manual font configuration")
        else:
            i += 1


def managed_block(values):
    return BEGIN + ":root {\n" + "".join(
        "  --tf-font-" + name + ": " + values[name] + ";\n"
        for name in ("family", "size") if name in values
    ) + "}\n" + END


def compose(original, patch):
    data = original if original is not None else b""
    if b"\0" in data:
        raise ValueError("NUL bytes or non-UTF-8 encodings are unsupported")
    bom = BOM if data.startswith(BOM) else b""
    text = data[len(bom):].decode("utf-8")
    values = {}
    if MARKER in text:
        end = text.find(END) + len(END)
        block = text[:end]
        if not text.startswith(BEGIN) or text.count(MARKER) != 2 or END not in text:
            raise ValueError("malformed, repeated, reversed, or displaced managed font markers")
        body = block[len(BEGIN):-len(END)]
        match = re.fullmatch(r":root \{\n((?:  --tf-font-(?:family|size): [^\n]+;\n){1,2})\}\n", body)
        if not match:
            raise ValueError("edited managed font block; keep custom CSS outside its markers")
        for name, value in re.findall(r"  --tf-font-(family|size): ([^\n]+);\n", match[1]):
            if name in values:
                raise ValueError("repeated managed font declaration")
            values[name] = font_value(name, value)
        if managed_block(values) != block:
            raise ValueError("edited managed font block; keep its generated format")
        text = text[end:]
    scan_css(text)
    values.update(patch)
    return bom + managed_block(values).encode("utf-8") + text.encode("utf-8")


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class Snapshot:
    data: Optional[bytes]
    identity: Optional[tuple]


def snapshot(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return Snapshot(None, None)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("config.css must be a regular file, not a symlink or directory")
    data = path.read_bytes()
    if identity(path.lstat()) != identity(info):
        raise ValueError("config.css changed while reading it; retry with Firefox closed")
    return Snapshot(data, identity(info))


def chrome_identity(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("chrome must be a directory, not a symlink")
    return (info.st_dev, info.st_ino)


@dataclass(frozen=True)
class ConfigPlan:
    path: Path
    original: Snapshot
    chrome: Optional[tuple]
    desired: bytes

    def recheck(self):
        current = chrome_identity(self.path.parent)
        if self.chrome is not None and self.chrome != current:
            raise ValueError("chrome directory changed during installation")
        if snapshot(self.path) != self.original:
            raise ValueError("config.css changed during installation; keeping the newer file")

    def commit(self):
        self.recheck()
        if self.desired == self.original.data:
            return
        mode = stat.S_IMODE(self.original.identity[2]) if self.original.identity else 0o600
        if self.original.data is not None:
            fd, backup = tempfile.mkstemp(prefix="config.css-", suffix=".bak", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    os.fchmod(stream.fileno(), mode)
                    stream.write(self.original.data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                os.unlink(backup)
                raise
            print("Previous config.css kept at " + backup, flush=True)
        stage = None
        try:
            fd, stage = tempfile.mkstemp(prefix=".config.css-textfox-", dir=self.path.parent)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(self.desired)
                stream.flush()
                os.fsync(stream.fileno())
            self.recheck()
            os.replace(stage, self.path)
        finally:
            if stage is not None and os.path.exists(stage):
                os.unlink(stage)


def install(config, patch, command):
    patch = {name: font_value(name, value) for name, value in patch.items()}
    chrome = chrome_identity(config.parent)
    original = snapshot(config)
    plan = ConfigPlan(config, original, chrome, compose(original.data, patch))
    subprocess.run(command, check=True)
    plan.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-family")
    parser.add_argument("--font-size")
    parser.add_argument("config", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or (args.font_family is None and args.font_size is None):
        parser.error("font flags and an installation callback are required")
    patch = {name: value for name, value in (("family", args.font_family), ("size", args.font_size))
             if value is not None}
    try:
        install(args.config, patch, args.command)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print("[!!] Font installation failed: " + str(error) +
              ". Install without font flags and edit chrome/config.css manually instead.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
