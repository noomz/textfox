#!/usr/bin/env python3
"""Run one painted suite split across several hosts at once (T36), e.g. two macOS VMs.

    run-split.py --host 'ssh admin@vm1' --host 'ssh admin@vm2' --tree '~/tf/fix' \
        --python '~/tf/venv/bin/python' --firefox '~/tf/ff160/Firefox.app/Contents/MacOS/firefox' \
        --logs DIR [--sync] -- test-surface-paint-live.py --themes art --art images

The suite's themes (`--themes SET` and `--theme` expanded through ff_themes) are dealt to
the hosts in turn; a suite without themes is split by browser Nova state (`--nova`). Each
host runs its share detached (nohup), so a dropped SSH connection does not stop it; the
runner polls for its exit code, then copies its log to DIR. Every share keeps the
known-bad control per V23, since a suite runs it for each theme it is given. Exits 0 only
when every share exits 0; a share that cannot start or report fails the run.

`--sync` first copies chrome/, user.js and tools/firefox-chrome/*.py of this checkout to
each host's tree (one directory above `--tree`'s tools/firefox-chrome).
"""
import argparse
import io
import shlex
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import ff_themes

ROOT = Path(__file__).resolve().parents[2]
POLL = 60  # seconds between exit-code checks


def shares(suite_args, hosts):
    """Split suite_args into one argument list per host (at most len(hosts))."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--theme', action='append', default=[])
    parser.add_argument('--themes')
    parser.add_argument('--nova', action='append')
    known, rest = parser.parse_known_args(suite_args)
    themes = known.theme + {'default': ff_themes.DEFAULT_THEMES, 'every': ff_themes.EVERY_THEME,
                            'art': ff_themes.ART_THEMES, None: []}[known.themes]
    if themes:
        nova = [a for n in known.nova or [] for a in ('--nova', n)]
        dealt = [themes[i::hosts] for i in range(hosts)]
        return [rest + nova + [a for t in share for a in ('--theme', t)] for share in dealt if share]
    novas = known.nova or ['false', 'true']
    dealt = [novas[i::hosts] for i in range(hosts)]
    return [rest + [a for n in share for a in ('--nova', n)] for share in dealt if share]


def sync(host, tree):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        tar.add(ROOT / 'chrome', 'chrome', filter=lambda i: None if '.omc' in Path(i.name).parts else i)
        tar.add(ROOT / 'user.js', 'user.js')
        for path in sorted((ROOT / 'tools' / 'firefox-chrome').glob('*.py')):
            tar.add(path, f'tools/firefox-chrome/{path.name}')
    top = f'{tree}/../..'
    subprocess.run([*shlex.split(host), f'mkdir -p {top} && cd {top} && tar xf -'],
                   input=buffer.getvalue(), check=True, timeout=300)


def run_share(index, host, args, opts, results):
    remote = f'{opts.tree}/run-split-{index}'
    command = (f'cd {opts.tree} && rm -f {remote}.rc {remote}.log && '
               f'FIREFOX={opts.firefox} PYTHONUNBUFFERED=1 nohup sh -c '
               + shlex.quote(f'{opts.python} {shlex.join(args)} > {remote}.log 2>&1; echo $? > {remote}.rc')
               + ' > /dev/null 2>&1 &')
    log = opts.logs / f'share-{index}.log'
    try:
        subprocess.run([*shlex.split(host), command], check=True, timeout=120)
        misses = 0
        while True:
            time.sleep(POLL)
            probe = subprocess.run([*shlex.split(host), f'cat {remote}.rc 2>/dev/null || echo running'],
                                   capture_output=True, text=True, timeout=120)
            state = probe.stdout.strip()
            if probe.returncode:
                misses += 1  # host briefly unreachable; the share keeps running there
                if misses > 30:
                    raise RuntimeError(f'host unreachable for {misses} polls')
                continue
            misses = 0
            if state != 'running':
                break
        fetched = subprocess.run([*shlex.split(host), f'cat {remote}.log'], capture_output=True, timeout=300)
        log.write_bytes(fetched.stdout)
        results[index] = int(state)
    except Exception as error:  # a share that cannot report is a failure, never inconclusive
        log.write_text(f'run-split: {error}\n')
        results[index] = f'error: {error}'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', action='append', required=True, help='SSH command prefix for one host')
    parser.add_argument('--tree', required=True, help="tools/firefox-chrome directory on each host")
    parser.add_argument('--python', required=True, help='Python on each host')
    parser.add_argument('--firefox', required=True, help='Firefox binary on each host')
    parser.add_argument('--logs', type=Path, required=True, help='local directory for each share log')
    parser.add_argument('--sync', action='store_true', help='copy this checkout to each host first')
    parser.add_argument('suite', nargs=argparse.REMAINDER, help='-- SUITE.py [suite args]')
    opts = parser.parse_args()
    suite = opts.suite[1:] if opts.suite[:1] == ['--'] else opts.suite
    if not suite:
        parser.error('no suite given')
    opts.logs.mkdir(parents=True, exist_ok=True)
    plan = shares(suite[1:], len(opts.host))
    if opts.sync:
        for host in opts.host:
            sync(host, opts.tree)
    results, threads, start = {}, [], time.time()
    for index, (host, args) in enumerate(zip(opts.host, plan)):
        print(f'share {index} on {host}: {shlex.join([suite[0], *args])}', flush=True)
        thread = threading.Thread(target=run_share, args=(index, host, [suite[0], *args], opts, results))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    minutes = (time.time() - start) / 60
    for index in range(len(plan)):
        log = (opts.logs / f'share-{index}.log').read_text(errors='replace').splitlines()
        verdicts = [line for line in log if line.startswith(('PASS', 'FAIL'))] or log[-3:]
        print(f'share {index}: exit {results.get(index)}; runs {sum(l.startswith("{") for l in log)}', flush=True)
        for line in verdicts:
            print(f'  {line}')
    ok = all(results.get(i) == 0 for i in range(len(plan)))
    print(f'{"PASS" if ok else "FAIL"}: {len(plan)} shares in {minutes:.1f} min', flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
