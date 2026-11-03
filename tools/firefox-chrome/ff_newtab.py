#!/usr/bin/env python3
"""Inspect initialized about:newtab in a disposable Marionette profile."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

from ff_inspect import connect

PREFERENCES = [
    "browser.nova.enabled",
    "browser.newtabpage.activity-stream.nova.enabled",
    "browser.newtabpage.activity-stream.testing.shouldInitializeFeeds",
    "browser.newtabpage.activity-stream.widgets.enabled",
]

JS = r"""
const selectors = [".nova-enabled", ".classic-enabled", ".logo-and-wordmark",
  ".wordmark", ".search-wrapper", ".top-site-outer", ".tile",
  ".widgets-section-container"];
const properties = ["display", "visibility", "color", "background-color",
  "border", "border-radius", "box-shadow"];
return {
  url: location.href,
  initialized: !!document.querySelector("#root > *"),
  selectors: Object.fromEntries(selectors.map(selector => {
    const element = document.querySelector(selector);
    if (!element) return [selector, null];
    const style = getComputedStyle(element);
    return [selector, Object.fromEntries(properties.map(p => [p, style.getPropertyValue(p)]))];
  })),
};
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=2828)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args()
    m = connect(args.port)
    try:
        m.set_context("chrome")
        profile = m.execute_script("return Services.dirsvc.get('ProfD', Ci.nsIFile).path", sandbox="system")
        try:
            os.kill(args.pid, 0)
        except ProcessLookupError:
            sys.exit("error: launched Firefox has exited")
        if Path(profile).resolve() != Path(args.profile).resolve():
            sys.exit("error: Marionette connected to an unexpected Firefox profile")
        m.execute_script('gBrowser.selectedTab = gBrowser.addTrustedTab("about:newtab")', sandbox="system")
        m.set_context("content")
        m.switch_to_window(m.window_handles[-1])
        deadline = time.monotonic() + 30
        while True:
            result = m.execute_script(JS)
            if result["url"] == "about:newtab" and result["initialized"]:
                break
            if time.monotonic() >= deadline:
                sys.exit(f"error: new-tab did not initialize: {result['url']}")
            time.sleep(0.25)
        m.set_context("chrome")
        result["preferences"] = m.execute_script(
            "return Object.fromEntries(arguments[0].map(p => [p, Services.prefs.getBoolPref(p, null)]))",
            script_args=[PREFERENCES], sandbox="system",
        )
        result["defaultPreferences"] = m.execute_script(
            "const branch = Services.prefs.getDefaultBranch(''); return Object.fromEntries(arguments[0].map(p => [p, branch.getBoolPref(p, null)]))",
            script_args=[PREFERENCES], sandbox="system",
        )
        result["firefox"] = m.execute_script(
            "return {version: Services.appinfo.version, buildID: Services.appinfo.appBuildID, platform: Services.appinfo.OS}",
            sandbox="system",
        )
        print(json.dumps(result, indent=2))
    finally:
        m.delete_session()


if __name__ == "__main__":
    main()
