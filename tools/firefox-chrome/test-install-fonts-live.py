#!/usr/bin/env python3
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

from ff_inspect import connect
from paint_display import open_display

ROOT = Path(__file__).resolve().parents[2]


def install(profile, *flags):
    result = subprocess.run([os.environ.get("TF_TEST_SHELL", "bash"), str(ROOT / "tf-install.sh"),
                             "--profile", str(profile), *flags], cwd=ROOT,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or "Installation completed" not in result.stdout:
        raise RuntimeError("installer failed:\n" + result.stdout + result.stderr)


def check(display, profile, port, label, expected_family, computed_family, expected_size, output):
    env = dict(display.firefox_env, MOZ_REMOTE_ALLOW_SYSTEM_ACCESS="1")
    log = (output / (label + "-firefox.log")).open("w")
    process = subprocess.Popen(display.firefox + ["--marionette", "-remote-allow-system-access",
                               "--new-instance", "--profile", str(profile), "about:blank"],
                               env=env, stdout=log, stderr=log, **display.popen_options())
    m = None
    try:
        m = connect(port)
        m.set_context("chrome")
        deadline = time.monotonic() + 30
        while not m.execute_script("return gBrowserInit.delayedStartupFinished", sandbox="system"):
            if time.monotonic() >= deadline:
                raise RuntimeError("Firefox startup did not finish")
            time.sleep(.1)
        meta = m.execute_script("""
            return {profile:Services.dirsvc.get('ProfD', Ci.nsIFile).path,
              version:Services.appinfo.version, build:Services.appinfo.appBuildID,
              os:Services.appinfo.OS, wayland:Services.env.get('WAYLAND_DISPLAY'),
              headless:Cc['@mozilla.org/gfx/info;1'].getService(Ci.nsIGfxInfo).isHeadless};
            """, sandbox="system")
        if process.poll() is not None or Path(meta["profile"]).resolve() != profile.resolve() or meta["headless"]:
            raise RuntimeError("unexpected Firefox process or profile: " + json.dumps(meta))
        display.bind(m)
        targets = m.execute_script("""
            return ['body', '#nav-bar', '#urlbar-input', '.tab-label'].map(selector => {
              const node = document.querySelector(selector), cs = node && getComputedStyle(node);
              return {selector, found:!!node, family:cs?.fontFamily, size:cs?.fontSize};
            });
            """, sandbox="system")
        actual = m.execute_script("""
            const cs = getComputedStyle(document.body), root = getComputedStyle(document.documentElement);
            return {family:cs.fontFamily, size:cs.fontSize,
              variableFamily:root.getPropertyValue('--tf-font-family').trim(),
              variableSize:root.getPropertyValue('--tf-font-size').trim()};
            """, sandbox="system")
        capture = output / (label + ".ppm")
        display.capture(capture)
        result = {"case": label, "firefox": meta, "computed": actual, "capture": str(capture),
                  "port": port, "pid": process.pid, "targets": targets}
        print(json.dumps(result), flush=True)
        if (actual != {"family": computed_family, "size": expected_size,
                       "variableFamily": expected_family, "variableSize": expected_size}
                or any(not t["found"] or t["family"] != computed_family or t["size"] != expected_size
                       for t in targets)):
            raise AssertionError("font precedence mismatch: " + json.dumps(result))
        return result
    finally:
        try:
            if m:
                m.delete_session()
        finally:
            display.stop_firefox(process)
            log.close()


def main():
    default_family = next(line.partition(":")[2].partition(";")[0].strip()
                          for line in (ROOT / "chrome/defaults.css").read_text().splitlines()
                          if line.lstrip().startswith("--tf-font-family:"))
    parent = Path(os.environ.get("TF_FONT_ARTIFACTS", tempfile.gettempdir()))
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="tf-installer-fonts-live-", dir=parent))
    print(json.dumps({"artifacts": str(output)}), flush=True)
    with tempfile.TemporaryDirectory(prefix="tf-installer-font-profile-") as name:
        profile = Path(name)
        install(profile)
        config = profile / "chrome/config.css"
        if config.exists():
            raise AssertionError("no-flag fresh install created config.css")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with (profile / "user.js").open("a") as prefs:
            prefs.write('user_pref("marionette.port", %d);\n' % port)
            prefs.write('user_pref("browser.shell.checkDefaultBrowser", false);\n')
        env = os.environ.copy()
        display_dir = output / "display"
        display_dir.mkdir(mode=0o700)
        display = open_display(display_dir, env)
        try:
            results = [check(display, profile, port, "defaults", default_family, default_family.replace('"', ''), "14px", output)]
            install(profile, "--font-family", '"JetBrains Mono", monospace', "--font-size", "13px")
            results.append(check(display, profile, port, "flags", '"JetBrains Mono", monospace', "JetBrains Mono, monospace", "13px", output))
            with config.open("ab") as stream:
                stream.write(b":root { --tf-font-family: serif; --tf-font-size: 19px; }")
            original = config.read_bytes()
            install(profile, "--font-size", "13px")
            if config.read_bytes() != original:
                raise AssertionError("repeated flags rewrote the later user declarations")
            results.append(check(display, profile, port, "later-user-root", "serif", "serif", "19px", output))
            (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        finally:
            display.close()
    print("default < installer flags < later unlayered equally specific :root verified", flush=True)


if __name__ == "__main__":
    main()
