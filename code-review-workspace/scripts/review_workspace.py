#!/usr/bin/env python3
"""Build an offline, single-file HTML code-review workspace from a real git diff.

Subcommands (run `<subcommand> --help` for options):
  collect  Resolve the review scope, then write diff.patch, manifest.json and a
           content.json skeleton into a work directory.
  render   Validate content.json against the manifest, render the HTML and
           self-check it. Nothing is written when validation fails.
  cleanup  Delete the temporary work directory (including a temporary PR clone).

Python 3.9+, standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from html.parser import HTMLParser

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL_PATH = os.path.join(SKILL_DIR, 'assets', 'review-shell.html')
MARKER = '.code-review-workspace'
MANIFEST_SCHEMA = 'code-review-workspace/manifest@1'
CONTENT_SCHEMA = 'code-review-workspace/content@1'
OUTPUT_DIR = 'analysis'
TODO = '<TODO>'
TESTS_DOMAIN = 'Tests'
RISK_LEVELS = {'High': 'high', 'Medium': 'watch', 'Low': 'info'}
DOMAIN_ACCENT = {'Frontend': 'frontend', 'Runtime Config': 'runtime', 'Infrastructure': 'runtime', 'Build & CI': 'runtime'}

TICKET_RE = re.compile(r'(?<![A-Za-z0-9])([A-Z][A-Z0-9]+-\d+)(?![A-Za-z0-9])')
CJK_RE = re.compile('[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]')
HUNK_RE = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$')
PR_URL_RE = re.compile(r'^https?://([^/\s]+)/([^/\s]+)/([^/\s]+)/pull/(\d+)(?:[/?#]\S*)?$')
REMOTE_RE = re.compile(r'^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?([^/:]+)(?::\d+)?[:/](.+?)(?:\.git)?/?$')

GIT_CONFIG = ['-c', 'core.quotePath=false', '-c', 'diff.noprefix=false',
              '-c', 'diff.mnemonicPrefix=false', '-c', 'diff.relative=false']
DIFF_FLAGS = ['--no-color', '--no-ext-diff', '--no-textconv', '--find-renames', '--find-copies',
              '--unified=3', '--src-prefix=a/', '--dst-prefix=b/']

TEST_DIR_RE = re.compile(r'(^|/)(__tests__|__mocks__|__snapshots__|tests?|testFixtures|'
                         r'src/(androidTest|integrationTest|it))(/|$)')
TEST_FILE_RE = re.compile(r'(\.(test|spec|e2e|cy)\.[A-Za-z0-9]+$)|((Test|Tests|IT|Spec)\.(kt|kts|java|scala|groovy)$)|'
                          r'(^test_[^/]*\.py$)|(_test\.(py|go)$)|(_spec\.rb$)|(\.snap$)')
JS_EXTS = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.vue', '.svelte']
SECRET_PATTERNS = ['.env', '.env.*', '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore', 'id_rsa*', 'id_dsa*',
                   'id_ecdsa*', 'id_ed25519*', '.npmrc', '.netrc', '.pypirc', 'credentials.json', '*.credentials']
SECRET_TEMPLATES = ['*.example', '*.sample', '*.template', '*.dist', '*.pub']
CODE_SPAN_RE = re.compile(r'`[^`]*`')
# Evidence stays in ledger.md: labels like "Evidence:", file:line / #L12 references, and L10-L20 line ranges.
EVIDENCE_RE = re.compile(
    r'(?i:\bevidence\s*[:：])|证据\s*[:：]'
    r'|\.(?:kts?|java|scala|groovy|tsx?|jsx?|mjs|cjs|vue|svelte|py|go|rb|cs|swift|json|ya?ml|properties|gradle|xml|html?'
    r'|s?css|less|sql|tf|tfvars|sh|md)(?::\d+|#L\d+)'
    r'|(?:[#:]|·\s*)L\d+\b|\bL\d+\s*[–—-]\s*L?\d+\b')

# Static UI strings rendered by this script; content strings are added at render time.
FIXED_EN = {
    '结论': 'Conclusion',
    '行为变化': 'Behavior changes',
    '实现链路': 'Implementation flow',
    '代码改动': 'Code changes',
    '数据契约': 'Data contract',
    '风险关注': 'Risks',
    '风险与关注点': 'Risks and review points',
    '场景': 'Scenario',
    '旧行为': 'Old behavior',
    '新行为': 'New behavior',
    '边界': 'Boundary',
    '表达': 'Representation',
    '约束': 'Constraint',
    '基于真实 diff 生成：': 'Generated from the real diff:',
    '。评论仅保存在当前浏览器，不会同步到 GitHub。': '. Comments are stored only in this browser and are not synchronized to GitHub.',
    '评论仅保存在当前浏览器，不会同步到 GitHub；请勿写入凭据或客户数据。':
        'Comments are stored only in this browser and are not synchronized to GitHub. Do not enter credentials or customer data.',
}
# (id, nav label, section title); "changes" is always present.
SECTIONS = [('behavior', '行为变化', '行为变化'), ('flow', '实现链路', '实现链路'), ('changes', '代码改动', '代码改动'),
            ('contract', '数据契约', '数据契约'), ('risks', '风险关注', '风险与关注点')]


class Fail(Exception):
    pass


# ---------------------------------------------------------------- utilities

def esc(text):
    return html.escape(text, quote=True)


def js_json(value):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    for old, new in (('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        text = text.replace(old, new)
    return text


def sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def plural(count, word):
    return f'{count} {word}' + ('' if count == 1 else 's')


def sanitize(text):
    text = re.sub(r'[^A-Za-z0-9._-]+', '-', text).strip('-.')
    return re.sub(r'-{2,}', '-', text)


def read_json(path):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise Fail(f'missing file: {path}')
    except json.JSONDecodeError as error:
        raise Fail(f'{path} is not valid JSON: {error}')


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(text)


def run(cmd, cwd=None, env=None, input_bytes=None, timeout=None):
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, input=input_bytes, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        raise Fail(f'command not found: {cmd[0]}')
    except subprocess.TimeoutExpired:
        raise Fail(f'command timed out after {timeout}s: {" ".join(cmd)}')
    return proc.returncode, proc.stdout.decode('utf-8', 'replace'), proc.stderr.decode('utf-8', 'replace').strip()


def git(repo, *args, env=None, input_bytes=None, timeout=None):
    code, out, err = run(['git', *GIT_CONFIG, '-C', repo, *args], env=env, input_bytes=input_bytes, timeout=timeout)
    if code != 0:
        raise Fail(f'git {" ".join(args)} failed: {err}')
    return out


def git_try(repo, *args, timeout=None):
    code, out, _ = run(['git', *GIT_CONFIG, '-C', repo, *args], timeout=timeout)
    return out.strip() if code == 0 else None


def toplevel(path):
    top = git_try(path, 'rev-parse', '--show-toplevel')
    if not top:
        raise Fail(f'not inside a git repository: {path}')
    return top


# ---------------------------------------------------------------- scope resolution

def parse_pr_url(url):
    match = PR_URL_RE.match(url.strip())
    if not match:
        raise Fail('not a pull request URL; expected https://<host>/<owner>/<repo>/pull/<number>')
    host, owner, repo, number = match.groups()
    repo = repo[:-4] if repo.endswith('.git') else repo
    return {'host': host.lower(), 'owner': owner, 'repo': repo, 'number': int(number),
            'url': f'https://{host}/{owner}/{repo}/pull/{number}'}


def gh_pr_view(pr):
    fields = 'number,title,body,state,baseRefName,baseRefOid,headRefName,headRefOid,url'
    code, out, err = run(['gh', 'pr', 'view', pr['url'], '--json', fields], timeout=120)
    if code != 0:
        raise Fail(f'gh pr view failed: {err}\nIf this is an authentication problem, ask the user to run '
                   f'`gh auth login --hostname {pr["host"]}` themselves.')
    return json.loads(out)


def find_local_repo(start, pr):
    top = git_try(start, 'rev-parse', '--show-toplevel')
    if not top:
        return None, None
    want = f'{pr["owner"]}/{pr["repo"]}'.lower()
    for line in (git_try(top, 'remote', '-v') or '').splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        match = REMOTE_RE.match(parts[1])
        if not match:
            continue
        host, path = match.group(1).lower(), match.group(2).lower()
        if '/'.join(path.split('/')[-2:]) == want and (host == pr['host'] or '.' not in host):
            return top, parts[0]
    return None, None


def clone_repo(pr, work):
    dest = os.path.join(work, 'repo')
    code, _, err = run(['gh', 'repo', 'clone', f'https://{pr["host"]}/{pr["owner"]}/{pr["repo"]}', dest,
                        '--', '--filter=blob:none', '--no-checkout', '--quiet'], timeout=1800)
    if code != 0:
        raise Fail(f'gh repo clone failed: {err}')
    return dest, 'origin'


def fetch_sha(repo, remote, ref):
    git(repo, 'fetch', '--quiet', '--no-tags', remote, ref, timeout=1800)
    return git(repo, 'rev-parse', 'FETCH_HEAD^{commit}').strip()


def maybe_fetch_base(repo, base):
    remote, _, branch = base.partition('/')
    if not branch:
        return None
    if remote not in (git_try(repo, 'remote') or '').split():
        return f'did not fetch {base}: no remote named {remote!r}; using the local ref'
    code, _, err = run(['git', '-C', repo, 'fetch', '--quiet', '--no-tags', remote, branch], timeout=180)
    return None if code == 0 else f'could not fetch {base}; using the local ref ({err.splitlines()[-1] if err else "no output"})'


def branch_for(repo, mode, head_sha):
    if mode == 'worktree':
        return git_try(repo, 'branch', '--show-current') or None
    name = git_try(repo, 'name-rev', '--name-only', '--no-undefined', '--refs=refs/heads/*', head_sha)
    return re.sub(r'[~^].*$', '', name) if name else None


def matches_any(path, patterns):
    base = path.rsplit('/', 1)[-1]
    for pattern in patterns:
        if pattern.endswith('/'):
            if path.startswith(pattern):
                return True
        elif fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(base, pattern):
            return True
    return False


def is_excluded(path, patterns):
    return path == OUTPUT_DIR or path.startswith(OUTPUT_DIR + '/') or matches_any(path, patterns)


def build_worktree_index(repo, work, excludes, allowed):
    """Snapshot tracked edits + untracked files into a temporary index; the user's index is untouched."""
    index = os.path.join(work, 'worktree.index')
    env = dict(os.environ, GIT_INDEX_FILE=index, GIT_LITERAL_PATHSPECS='1')
    real_index = git(repo, 'rev-parse', '--git-path', 'index').strip()
    real_index = real_index if os.path.isabs(real_index) else os.path.join(repo, real_index)
    if os.path.isfile(real_index):
        shutil.copyfile(real_index, index)  # keeps skip-worktree bits (sparse checkouts) and the stat cache
    else:
        git(repo, 'read-tree', 'HEAD', env=env)
    git(repo, 'add', '-u', env=env)
    others = [p for p in git(repo, 'ls-files', '-z', '--others', '--exclude-standard', env=env).split('\0')
              if p and not p.endswith('/')]
    included = sorted(p for p in others if not is_excluded(p, excludes))
    excluded = sorted(p for p in others if is_excluded(p, excludes))
    suspicious = [p for p in included if matches_any(p, SECRET_PATTERNS)
                  and not matches_any(p, SECRET_TEMPLATES) and not matches_any(p, allowed)]
    if suspicious:  # checked before anything is hashed or written, so the content never leaves the file
        raise Fail('untracked files that may hold secrets: ' + ', '.join(suspicious) +
                   "\nRe-run with --exclude '<glob>' (recommended), or --allow '<glob>' only if the user confirms the file "
                   'is safe to show in the review.')
    if included:
        git(repo, 'add', '--pathspec-from-file=-', '--pathspec-file-nul', env=env,
            input_bytes=('\0'.join(included) + '\0').encode('utf-8'))
    tree = git(repo, 'write-tree', env=env).strip()
    changed = tree != git(repo, 'rev-parse', 'HEAD^{tree}').strip()
    return env, included, excluded, tree, changed


# ---------------------------------------------------------------- diff parsing

def parse_name_status(out):
    tokens, entries, i = out.split('\0'), [], 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i]
        if status[0] in 'RC':
            entries.append({'status': status[0], 'score': int(status[1:] or 0), 'oldPath': tokens[i + 1], 'path': tokens[i + 2]})
            i += 3
        else:
            entries.append({'status': status[0], 'score': None, 'oldPath': None, 'path': tokens[i + 1]})
            i += 2
    return entries


def parse_numstat(out):
    tokens, entries, i = out.split('\0'), [], 0
    while i < len(tokens) and tokens[i]:
        added, deleted, path = tokens[i].split('\t', 2)
        if path == '':
            path = tokens[i + 2]
            i += 3
        else:
            i += 1
        binary = added == '-'
        entries.append({'path': path, 'binary': binary,
                        'additions': None if binary else int(added), 'deletions': None if binary else int(deleted)})
    return entries


def split_patch(patch):
    sections, current = [], None
    for line in patch.split('\n'):  # never splitlines(): it also splits on \r, \x0c, \u2028 ...
        if line.startswith('diff --git '):
            current = [line]
            sections.append(current)
        elif current is not None:
            current.append(line)
    return sections


def parse_section(lines):
    info = {'binary': False, 'oldMode': None, 'newMode': None}
    i, n = 1, len(lines)
    while i < n and not lines[i].startswith('@@ '):
        line = lines[i]
        if line.startswith('Binary files ') or line.startswith('GIT binary patch'):
            info['binary'] = True
        elif line.startswith('old mode '):
            info['oldMode'] = line[9:].strip()
        elif line.startswith('new mode '):
            info['newMode'] = line[9:].strip()
        i += 1
    hunks = []
    while i < n:
        header = lines[i]
        if not header and not any(lines[i:]):
            break
        match = HUNK_RE.match(header)
        if not match:
            raise Fail(f'unexpected patch line: {header[:120]!r}')
        old_no, new_no = int(match.group(1)), int(match.group(3))
        old_left = int(match.group(2)) if match.group(2) is not None else 1
        new_left = int(match.group(4)) if match.group(4) is not None else 1
        rows = []
        i += 1
        while i < n and (old_left > 0 or new_left > 0):
            line = lines[i]
            tag, text = line[:1], line[1:]
            if tag == '\\':
                rows.append(['meta', None, None, line])
            elif tag == ' ' or line == '':
                rows.append(['context', old_no, new_no, text])
                old_no, new_no, old_left, new_left = old_no + 1, new_no + 1, old_left - 1, new_left - 1
            elif tag == '-':
                rows.append(['deletion', old_no, None, text])
                old_no, old_left = old_no + 1, old_left - 1
            elif tag == '+':
                rows.append(['addition', None, new_no, text])
                new_no, new_left = new_no + 1, new_left - 1
            else:
                raise Fail(f'unexpected hunk line: {line[:120]!r}')
            i += 1
        while i < n and lines[i].startswith('\\'):
            rows.append(['meta', None, None, lines[i]])
            i += 1
        if old_left != 0 or new_left != 0:
            raise Fail(f'truncated or malformed hunk: {header[:120]!r}')
        hunks.append({'header': header, 'rows': rows})
    return info, hunks


def header_matches(header, entry):
    if '"' in header:  # C-quoted path; order equality is still guaranteed by git
        return True
    return header.endswith(' b/' + entry['path'])  # deletions also print "a/x b/x"


def assemble_files(name_status, numstat, sections):
    if not (len(name_status) == len(numstat) == len(sections)):
        raise Fail(f'git outputs disagree: {len(name_status)} name-status / {len(numstat)} numstat / {len(sections)} patch sections')
    files, parsed = [], []
    for entry, stats, section in zip(name_status, numstat, sections):
        if entry['path'] != stats['path'] or not header_matches(section[0], entry):
            raise Fail(f'patch order mismatch at {entry["path"]}')
        info, hunks = parse_section(section)
        additions = sum(1 for hunk in hunks for row in hunk['rows'] if row[0] == 'addition')
        deletions = sum(1 for hunk in hunks for row in hunk['rows'] if row[0] == 'deletion')
        binary = stats['binary'] or info['binary']
        if not binary and (additions, deletions) != (stats['additions'], stats['deletions']):
            raise Fail(f'{entry["path"]}: parsed +{additions}/-{deletions} but numstat says +{stats["additions"]}/-{stats["deletions"]}')
        files.append({'path': entry['path'], 'oldPath': entry['oldPath'], 'status': entry['status'], 'score': entry['score'],
                      'binary': binary, 'additions': None if binary else additions, 'deletions': None if binary else deletions,
                      'hunks': len(hunks), 'oldMode': info['oldMode'], 'newMode': info['newMode']})
        parsed.append(hunks)
    return files, parsed


# ---------------------------------------------------------------- test pairing heuristics

def is_test_path(path):
    return bool(TEST_DIR_RE.search(path) or TEST_FILE_RE.search(path.rsplit('/', 1)[-1]))


def test_counterpart(path, all_paths):
    folder, _, base = path.rpartition('/')
    parent = folder.rpartition('/')[0]
    in_test_dir = folder.endswith(('__tests__', '__mocks__'))
    candidates = []

    def join(directory, name):
        return f'{directory}/{name}' if directory else name

    match = re.match(r'^(.*)\.(test|spec|e2e|cy)(\.[A-Za-z0-9]+)$', base)
    if match:
        stem, ext = match.group(1), match.group(3)
        for directory in ([folder, parent] if in_test_dir else [folder]):
            candidates += [join(directory, stem + e) for e in [ext] + JS_EXTS]
    elif in_test_dir:
        stem, dot, ext = base.rpartition('.')
        candidates += [join(parent, base)] + ([join(parent, stem + e) for e in JS_EXTS] if dot else [])
    match = re.match(r'^(.+?)(Tests?|IT|Spec)(\.(?:kt|kts|java|scala|groovy))$', base)
    if match:
        name = match.group(1) + match.group(3)
        main_dir = re.sub(r'(^|/)src/(test|integrationTest|it|testFixtures)(/|$)', r'\1src/main\3', folder)
        candidates += [join(main_dir, name), join(folder, name)]
    match = re.match(r'^(?:test_(.+)|(.+)_test)\.(py|go)$', base)
    if match:
        name = (match.group(1) or match.group(2)) + '.' + match.group(3)
        candidates += [join(folder, name), join(parent, name)]
        same_name = [p for p in all_paths if p.rsplit('/', 1)[-1] == name]
        if len(same_name) == 1:
            candidates.append(same_name[0])
    if base.endswith('.snap') and folder.endswith('__snapshots__'):
        candidates.append(join(parent, base[:-5]))
    for candidate in candidates:
        if candidate != path and candidate in all_paths:
            return candidate
    return None


# ---------------------------------------------------------------- collect

def output_path(root, ticket, pr_number, branch, head_sha):
    folder = ticket or (f'PR-{pr_number}' if pr_number else '') or sanitize(branch or '') or head_sha[:12]
    topic = (branch or '').replace(ticket, '') if ticket else (branch or '')
    topic = sanitize(topic.replace('/', '-')) or head_sha[:12]
    return os.path.join(root, OUTPUT_DIR, folder, f'{topic}_code-review.html')


def skeleton(files):
    pair = {'zh': TODO, 'en': TODO}
    return {
        'schema': CONTENT_SCHEMA,
        'meta': {'title': TODO, 'subtitle': TODO},
        'conclusion': pair,
        'behavior': {'summary': pair, 'rows': [{'scenario': pair, 'before': pair, 'after': pair}]},
        'flow': {'summary': pair, 'steps': [{'title': pair, 'detail': pair}, {'title': pair, 'detail': pair}]},
        'changes': {'summary': pair, 'domains': [{'name': TODO, 'points': [pair], 'groups': [{
            'title': pair, 'summary': pair,
            'files': [{'path': f['path'], 'role': pair, 'units': [{'name': TODO, 'change': pair, 'reason': pair}]} for f in files],
        }]}]},
        'contract': {'summary': pair, 'rows': [{'boundary': pair, 'representation': pair, 'constraint': pair}]},
        'risks': {'summary': pair, 'items': [{'level': 'Medium', 'title': pair, 'detail': pair}]},
    }


def cmd_collect(args):
    work = os.path.abspath(args.work) if args.work else tempfile.mkdtemp(prefix='code-review-')
    os.makedirs(work, exist_ok=True)
    write_text(os.path.join(work, MARKER), 'created by code-review-workspace collect\n')
    notes, pr, cloned, env = [], None, False, None
    untracked, excluded, tree, has_worktree_changes = [], [], None, False

    if args.pr:
        pr = parse_pr_url(args.pr)
        meta = gh_pr_view(pr)
        pr.update({'title': meta.get('title') or '', 'body': meta.get('body') or '', 'state': meta.get('state'),
                   'baseRefName': meta['baseRefName'], 'headRefName': meta['headRefName']})
        repo, remote = find_local_repo(os.path.abspath(args.repo), pr)
        if not repo:
            repo, remote = clone_repo(pr, work)
            cloned = True
        head_sha = fetch_sha(repo, remote, f'refs/pull/{pr["number"]}/head')
        base_tip = fetch_sha(repo, remote, f'refs/heads/{pr["baseRefName"]}')
        # GitHub diffs a PR against its recorded base commit; for merged PRs the branch tip already contains the head.
        recorded = meta.get('baseRefOid')
        base_sha = recorded if recorded and git_try(repo, 'cat-file', '-e', recorded + '^{commit}') is not None else base_tip
        if base_sha != recorded:
            notes.append(f'recorded PR base {str(recorded)[:12]} is not available locally; using {pr["baseRefName"]} tip {base_tip[:12]}')
        if head_sha != meta['headRefOid']:
            notes.append(f'PR head moved: gh reported {meta["headRefOid"][:12]}, fetched {head_sha[:12]}; reviewing the fetched commit')
        base_label, mode, branch = f'{remote}/{pr["baseRefName"]}', 'pr', pr['headRefName']
    else:
        repo = toplevel(os.path.abspath(args.repo))
        base_label = args.base
        if not args.no_fetch:
            warning = maybe_fetch_base(repo, args.base)
            if warning:
                notes.append(warning)
        base_sha = git_try(repo, 'rev-parse', '--verify', '--quiet', args.base + '^{commit}')
        if not base_sha:
            default = git_try(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
            raise Fail(f'base ref {args.base!r} not found' + (f'; the remote default branch is {default} (pass --base {default})' if default else ''))
        if args.head:
            head_sha = git_try(repo, 'rev-parse', '--verify', '--quiet', args.head + '^{commit}')
            if not head_sha:
                raise Fail(f'commit {args.head!r} not found')
            mode = 'commit'
        else:
            head_sha, mode = git(repo, 'rev-parse', 'HEAD').strip(), 'worktree'
        branch = branch_for(repo, mode, head_sha)

    merge_base = git_try(repo, 'merge-base', base_sha, head_sha)
    if not merge_base:
        raise Fail('no merge base between base and head (unrelated histories or a shallow clone; try `git fetch --unshallow`)')

    if mode == 'worktree':
        env, untracked, excluded, tree, has_worktree_changes = build_worktree_index(repo, work, args.exclude, args.allow)
        rng = ['--cached', merge_base]
    else:
        if args.exclude or args.allow:
            raise Fail('--exclude/--allow only apply to untracked files in working-tree mode')
        rng = [merge_base, head_sha]

    name_status = parse_name_status(git(repo, 'diff', *DIFF_FLAGS, '--name-status', '-z', *rng, env=env))
    numstat = parse_numstat(git(repo, 'diff', *DIFF_FLAGS, '--numstat', '-z', *rng, env=env))
    patch = git(repo, 'diff', *DIFF_FLAGS, *rng, env=env)
    files, parsed = assemble_files(name_status, numstat, split_patch(patch))
    if not files:
        raise Fail('no changes between the base and the review head; nothing to review')
    paths = {f['path'] for f in files}
    for f in files:
        f['isTest'] = is_test_path(f['path'])
        f['testOf'] = test_counterpart(f['path'], paths) if f['isTest'] else None

    log = git(repo, 'log', '--no-color', '--reverse', '--format=%H%x1f%B%x1e', f'{merge_base}..{head_sha}')
    commits = []
    for record in log.split('\x1e'):
        record = record.strip('\n')
        if record:
            sha, _, message = record.partition('\x1f')
            commits.append({'sha': sha, 'message': message.strip()})

    ticket_match = TICKET_RE.search(branch or '')
    ticket = ticket_match.group(1) if ticket_match else None
    if args.out:
        out = os.path.abspath(args.out)
    else:
        root = repo
        if cloned:
            root = git_try(os.path.abspath(args.repo), 'rev-parse', '--show-toplevel') or os.path.abspath(args.repo)
        out = output_path(root, ticket, pr['number'] if pr else None, branch, head_sha)
    head_label = f'{head_sha}+worktree.{tree[:8]}' if has_worktree_changes else head_sha
    if pr:
        blob = f'https://{pr["host"]}/{pr["owner"]}/{pr["repo"]}/blob/'
        links = {'kind': 'url', 'head': f'{blob}{head_sha}/', 'base': f'{blob}{merge_base}/'}
    else:
        links = {'kind': 'relative'}
    totals = {'files': len(files), 'hunks': sum(f['hunks'] for f in files),
              'additions': sum(f['additions'] or 0 for f in files), 'deletions': sum(f['deletions'] or 0 for f in files)}
    manifest = {
        'schema': MANIFEST_SCHEMA, 'mode': mode, 'repoRoot': repo, 'clonedRepo': cloned,
        'baseRef': base_label, 'baseSha': base_sha, 'mergeBase': merge_base, 'headSha': head_sha, 'headLabel': head_label,
        'worktreeChanges': has_worktree_changes, 'branch': branch, 'ticket': ticket, 'pr': pr, 'commits': commits,
        'untrackedIncluded': untracked, 'untrackedExcluded': excluded, 'files': files, 'totals': totals,
        'diffSha256': sha256(patch), 'output': out, 'links': links,
        'generated': datetime.date.today().isoformat(), 'notes': notes,
    }
    write_text(os.path.join(work, 'diff.patch'), patch)
    content_path = os.path.join(work, 'content.json')
    fresh = json.dumps(skeleton(files), ensure_ascii=False, indent=2)
    manifest['skeletonSha256'] = sha256(fresh)
    previous_path = os.path.join(work, 'manifest.json')
    previous = read_json(previous_path).get('skeletonSha256') if os.path.exists(previous_path) else None
    if not os.path.exists(content_path) or (previous and sha256(open(content_path, encoding='utf-8').read()) == previous):
        write_text(content_path, fresh)  # replace only a skeleton nobody has edited yet
    write_text(previous_path, json.dumps(manifest, ensure_ascii=False, indent=2))
    print_collect_summary(manifest, work)


def print_collect_summary(m, work):
    t = m['totals']
    head = m['headSha'][:12] + (' + working tree' if m['worktreeChanges'] else '')
    print(f'Scope    : {m["mode"]} | base {m["baseRef"]} @ merge-base {m["mergeBase"][:12]} | head {head}')
    if m['pr']:
        print(f'PR       : #{m["pr"]["number"]} [{m["pr"].get("state")}] {m["pr"]["title"]} ({m["pr"]["url"]})')
    print(f'Commits  : {len(m["commits"])}')
    for commit in m['commits']:
        print(f'  {commit["sha"][:12]} {commit["message"].splitlines()[0] if commit["message"] else ""}')
    if m['untrackedIncluded']:
        print(f'Untracked included ({len(m["untrackedIncluded"])}) - check for secrets, build output, scratch files:')
        for path in m['untrackedIncluded'][:40]:
            print(f'  {path}')
    if m['untrackedExcluded']:
        print(f'Untracked excluded ({len(m["untrackedExcluded"])}): ' + ', '.join(m['untrackedExcluded'][:10]))
    print(f'Files    : {t["files"]} | hunks {t["hunks"]} | +{t["additions"]} -{t["deletions"]}')
    for f in m['files']:
        stats = 'binary' if f['binary'] else f'+{f["additions"]} -{f["deletions"]}'
        extra = f' (from {f["oldPath"]})' if f['oldPath'] else ''
        if f['isTest']:
            extra += f'  [test of {f["testOf"]}]' if f['testOf'] else '  [test, no counterpart in diff]'
        print(f'  {f["status"]} {stats:>12}  {f["path"]}{extra}')
    for note in m['notes']:
        print(f'NOTE     : {note}')
    after = '<working-tree file>' if m['mode'] == 'worktree' else f'git -C {m["repoRoot"]} show {m["headSha"][:12]}:<path>'
    print(f'Before   : git -C {m["repoRoot"]} show {m["mergeBase"][:12]}:<old path>')
    print(f'After    : {after}')
    print(f'Work dir : {work}  (diff.patch, manifest.json, content.json)')
    print(f'Output   : {m["output"]}')
    print(f'Storage  : code-review-comments:{m["headLabel"]}')


# ---------------------------------------------------------------- content validation

class Checker:
    def __init__(self):
        self.errors, self.warnings, self.pairs = [], [], {}

    def error(self, where, message):
        self.errors.append(f'{where}: {message}')

    def keys(self, where, obj, required, optional=()):
        if not isinstance(obj, dict):
            self.error(where, 'must be an object')
            return False
        for key in required:
            if key not in obj:
                self.error(where, f'missing "{key}"')
        unknown = set(obj) - set(required) - set(optional)
        if unknown:
            self.error(where, f'unknown keys {sorted(unknown)} (the contract has no such fields; remove them)')
        return True

    def plain(self, where, value, cjk_ok=True):
        if not isinstance(value, str) or not value.strip():
            self.error(where, 'must be a non-empty string')
            return ''
        value = ' '.join(value.split())
        if TODO in value:
            self.error(where, 'still contains the <TODO> placeholder')
        if not cjk_ok and CJK_RE.search(CODE_SPAN_RE.sub('', value)):
            self.error(where, 'must not contain CJK characters outside `backtick` code/path spans')
        citation = EVIDENCE_RE.search(value)
        if citation:
            self.error(where, f'{citation.group(0)!r} looks like an evidence citation; the page shows no evidence - keep it '
                              f'in ledger.md and describe the change in words')
        return value

    def pair(self, where, value, ui=True):
        if not self.keys(where, value, ('zh', 'en')):
            return ('', '')
        zh = self.plain(f'{where}.zh', value.get('zh'))
        en = self.plain(f'{where}.en', value.get('en'), cjk_ok=False)
        if ui and zh and en:
            previous = self.pairs.get(zh)
            if previous is not None and previous != en:
                self.error(where, f'zh text {zh!r} is already translated as {previous!r}; the page translates by exact zh '
                                  f'text, so reuse that en text or rephrase this zh string')
            self.pairs.setdefault(zh, en)
        return (zh, en)

    def items(self, where, value, minimum, maximum=None):
        if not isinstance(value, list):
            self.error(where, 'must be an array')
            return []
        if len(value) < minimum or (maximum is not None and len(value) > maximum):
            self.error(where, f'needs {minimum}{"-" + str(maximum) if maximum else "+"} items, found {len(value)}')
        return value


def validate(content, manifest):
    c = Checker()
    for zh, en in FIXED_EN.items():
        c.pairs[zh] = en
    c.keys('content', content, ('schema', 'meta', 'conclusion', 'behavior', 'changes', 'risks'), ('flow', 'contract'))
    if not isinstance(content, dict):
        return None, c
    if content.get('schema') != CONTENT_SCHEMA:
        c.error('schema', f'must be "{CONTENT_SCHEMA}"')
    model = {'sections': {}}

    meta = content.get('meta', {})
    if c.keys('meta', meta, ('title', 'subtitle')):
        model['title'] = c.plain('meta.title', meta.get('title'), cjk_ok=False)
        model['subtitle'] = c.plain('meta.subtitle', meta.get('subtitle'), cjk_ok=False)
    model['conclusion'] = c.pair('conclusion', content.get('conclusion'))

    def section(name, list_key, minimum, maximum, fields, optional_section):
        value = content.get(name)
        if value is None and optional_section:
            return
        if not c.keys(name, value, (list_key,), ('summary',)):
            return
        summary = c.pair(f'{name}.summary', value['summary']) if 'summary' in value else None
        rows = []
        for i, row in enumerate(c.items(f'{name}.{list_key}', value.get(list_key), minimum, maximum)):
            where = f'{name}.{list_key}[{i}]'
            extra = ('level',) if name == 'risks' else ()
            if c.keys(where, row, fields + extra):
                item = {field: c.pair(f'{where}.{field}', row.get(field)) for field in fields}
                if name == 'risks':
                    if row.get('level') not in RISK_LEVELS:
                        c.error(f'{where}.level', f'must be one of {list(RISK_LEVELS)}')
                    item['level'] = row.get('level')
                rows.append(item)
        model['sections'][name] = {'summary': summary, 'rows': rows}

    section('behavior', 'rows', 1, None, ('scenario', 'before', 'after'), False)
    section('flow', 'steps', 2, 5, ('title', 'detail'), True)
    section('contract', 'rows', 1, None, ('boundary', 'representation', 'constraint'), True)
    section('risks', 'items', 1, None, ('title', 'detail'), False)

    by_path = {f['path']: f for f in manifest['files']}
    placement, domains = {}, []
    changes = content.get('changes')
    if c.keys('changes', changes, ('summary', 'domains')):
        model['changesSummary'] = c.pair('changes.summary', changes.get('summary'))
        raw_domains = c.items('changes.domains', changes.get('domains'), 1)
        names = []
        for di, raw in enumerate(raw_domains):
            dwhere = f'changes.domains[{di}]'
            if not c.keys(dwhere, raw, ('name', 'points', 'groups')):
                continue
            name = c.plain(f'{dwhere}.name', raw.get('name'), cjk_ok=False)
            if name in names:
                c.error(f'{dwhere}.name', f'duplicate domain {name!r}; merge them')
            names.append(name)
            points = [c.pair(f'{dwhere}.points[{i}]', p) for i, p in enumerate(c.items(f'{dwhere}.points', raw.get('points'), 1, 4))]
            groups = []
            for gi, graw in enumerate(c.items(f'{dwhere}.groups', raw.get('groups'), 1)):
                gwhere = f'{dwhere}.groups[{gi}]'
                if not c.keys(gwhere, graw, ('title', 'summary', 'files')):
                    continue
                group = {'title': c.pair(f'{gwhere}.title', graw.get('title')),
                         'summary': c.pair(f'{gwhere}.summary', graw.get('summary')), 'files': []}
                for fi, fraw in enumerate(c.items(f'{gwhere}.files', graw.get('files'), 1)):
                    fwhere = f'{gwhere}.files[{fi}]'
                    if not c.keys(fwhere, fraw, ('path', 'role', 'units'), ('pairedWith',)):
                        continue
                    path = fraw.get('path')
                    paired_raw = fraw.get('pairedWith')
                    if isinstance(paired_raw, str):
                        paired_raw = [paired_raw]
                    if paired_raw is not None and (not isinstance(paired_raw, list) or not paired_raw
                                                   or not all(isinstance(p, str) for p in paired_raw)):
                        c.error(f'{fwhere}.pairedWith', 'must be a changed file path, a non-empty list of paths, or null')
                        continue
                    if path not in by_path:
                        c.error(f'{fwhere}.path', f'{path!r} is not a changed file in this diff')
                        continue
                    if path in placement:
                        c.error(f'{fwhere}.path', f'{path} already appears at {placement[path]["where"]}; each file must appear exactly once')
                        continue
                    units = []
                    for ui, uraw in enumerate(c.items(f'{fwhere}.units', fraw.get('units'), 1)):
                        uwhere = f'{fwhere}.units[{ui}]'
                        if c.keys(uwhere, uraw, ('name', 'change', 'reason')):
                            units.append({'name': c.plain(f'{uwhere}.name', uraw.get('name')),
                                          'change': c.pair(f'{uwhere}.change', uraw.get('change'), ui=False),
                                          'reason': c.pair(f'{uwhere}.reason', uraw.get('reason'), ui=False)})
                    entry = {'path': path, 'role': c.pair(f'{fwhere}.role', fraw.get('role')), 'units': units}
                    test_of = by_path[path]['testOf']
                    placement[path] = {'where': fwhere, 'domain': di, 'domainName': name, 'group': gi, 'index': fi,
                                       'paired': paired_raw if 'pairedWith' in fraw else ([test_of] if test_of else None),
                                       'explicit': 'pairedWith' in fraw}
                    group['files'].append(entry)
                groups.append(group)
            domains.append({'name': name, 'accent': DOMAIN_ACCENT.get(name, 'backend'), 'points': points, 'groups': groups})
        if TESTS_DOMAIN in names and names.index(TESTS_DOMAIN) != len(names) - 1:
            c.error('changes.domains', f'the "{TESTS_DOMAIN}" domain must be the last domain')
    model['domains'] = domains

    missing = [p for p in by_path if p not in placement]
    for path in missing:
        c.error('changes', f'changed file missing from all groups: {path}')
    for path, where in placement.items():
        in_tests = where['domainName'] == TESTS_DOMAIN
        paired = where['paired']
        if paired is None:
            if by_path[path]['isTest'] and not in_tests:
                c.error(where['where'], f'{path} is a test with no implementation file in this diff: move it to the "{TESTS_DOMAIN}" '
                                        f'domain, or set "pairedWith" to the changed implementation file it verifies')
            elif not by_path[path]['isTest'] and in_tests:
                c.error(where['where'], f'{path} is not a test file; only unpaired tests belong in "{TESTS_DOMAIN}"')
            continue
        for target_path in paired:
            target = placement.get(target_path)
            if target is None:
                c.error(where['where'], f'pairedWith/test counterpart {target_path!r} is not placed in any group')
            elif (target['domain'], target['group']) != (where['domain'], where['group']):
                c.error(where['where'], f'{path} must be in the same group as {target_path} (tests sit with the implementation '
                                        f'they verify)' + ('' if where['explicit'] else '; if this pairing is wrong, set "pairedWith"'))
            elif target['index'] > where['index']:
                c.error(where['where'], f'{path} must come after {target_path} in its group')
    if len(model.get('conclusion', ('',))[0]) > 90:
        c.warnings.append('conclusion.zh is longer than 90 characters; the apex should be one sentence')
    return model, c


# ---------------------------------------------------------------- rendering

def render_rows(hunks):
    out = []
    for hunk in hunks:
        out.append(f'<tr class="diff-hunk"><td colspan="3">{esc(hunk["header"])}</td></tr>')
        for kind, old, new, text in hunk['rows']:
            if kind == 'meta':  # "\ No newline at end of file": the page shows only hunks and code lines
                continue
            marker = {'context': ' ', 'addition': '+', 'deletion': '-'}[kind]
            out.append(f'<tr class="diff-row {kind}"><td class="diff-line-number">{old or ""}</td>'
                       f'<td class="diff-line-number">{new or ""}</td><td class="diff-code">'
                       f'<span class="diff-marker">{marker}</span>{esc(text)}</td></tr>')
    return '\n'.join(out)


def file_href(manifest, f, out_path):
    links = manifest['links']
    if links['kind'] == 'url':
        return (links['base'] if f['status'] == 'D' else links['head']) + urllib.parse.quote(f['path'])
    target = os.path.join(manifest['repoRoot'], f['path'])
    return urllib.parse.quote(os.path.relpath(target, os.path.dirname(out_path)).replace(os.sep, '/'))


def stat_html(f):
    if f['binary']:
        return 'binary'
    return f'<span class="added">+{f["additions"]}</span> <span class="deleted">-{f["deletions"]}</span>'


def render_file(manifest, f, entry, hunks, out_path):
    letter = f'<span class="status-letter{" add" if f["status"] == "A" else ""}">{f["status"]}</span>'
    name = f['path'].rsplit('/', 1)[-1]
    detail = 'binary' if f['binary'] else f'{plural(f["hunks"], "hunk")} · {stat_html(f)}'
    return (f'<li class="file"><details class="file-diff"><summary class="file-summary">{letter}'
            f'<span class="file-path">{esc(name)}</span><span class="file-role">{esc(entry["role"][0])}</span>'
            f'<span class="diff-stat">{stat_html(f)}</span></summary><div class="diff-view"><div class="diff-file-header">'
            f'<a href="{esc(file_href(manifest, f, out_path))}">{esc(f["path"])}</a><span>{detail}</span></div>'
            f'<div class="diff-scroll"><table class="diff-table"><thead class="visually-hidden"><tr><th scope="col">Old line</th>'
            f'<th scope="col">New line</th><th scope="col">Code</th></tr></thead><tbody>{render_rows(hunks)}</tbody></table>'
            f'</div></div></details></li>')


def render_domains(manifest, model, hunks_by_path, out_path):
    by_path = {f['path']: f for f in manifest['files']}
    parts = []
    for domain in model['domains']:
        count = sum(len(g['files']) for g in domain['groups'])
        points = ''.join(f'<li>{esc(p[0])}</li>' for p in domain['points'])
        groups = []
        for index, group in enumerate(domain['groups'], 1):
            files = ''.join(render_file(manifest, by_path[e['path']], e, hunks_by_path[e['path']], out_path) for e in group['files'])
            groups.append(f'<details class="logic-group"><summary><span class="group-step">{index:02d}</span><span class="group-label">'
                          f'<strong>{esc(group["title"][0])}</strong><small>{esc(group["summary"][0])}</small></span>'
                          f'<span class="group-count">{plural(len(group["files"]), "file")}</span></summary>'
                          f'<ul class="files">{files}</ul></details>')
        parts.append(f'<article class="file-domain {domain["accent"]}"><header class="domain-header"><div class="domain-title">'
                     f'<h3>{esc(domain["name"])}</h3><span class="domain-count">{plural(count, "file")}</span></div>'
                     f'<ul class="domain-summary">{points}</ul></header><div class="domain-groups">{"".join(groups)}</div></article>')
    return ''.join(parts)


def render_section(sid, number, title, summary, body):
    lead = f'<p class="section-summary">{esc(summary[0])}</p>' if summary else ''
    return (f'<section id="{sid}"><div class="section-head"><div class="section-number">{number:02d}</div>'
            f'<div><h2>{esc(title)}</h2>{lead}</div></div>{body}</section>')


def table(css, headers, rows, fields):
    head = ''.join(f'<th>{esc(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{esc(row[f][0])}</td>' for f in fields) + '</tr>' for row in rows)
    return f'<table class="{css}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def section_body(sid, data):
    rows = data['rows']
    if sid == 'behavior':
        return table('behavior-table', ['场景', '旧行为', '新行为'], rows, ('scenario', 'before', 'after'))
    if sid == 'flow':
        steps = ''.join(f'<div class="flow-step"><b>{esc(f"{i}. " + s["title"][0])}</b><p>{esc(s["detail"][0])}</p></div>'
                        for i, s in enumerate(rows, 1))
        return f'<div class="flow">{steps}</div>'
    if sid == 'contract':
        return table('contract-table', ['边界', '表达', '约束'], rows, ('boundary', 'representation', 'constraint'))
    items = ''.join(f'<li class="risk-item"><span class="badge {RISK_LEVELS[r["level"]]}">{esc(r["level"])}</span><div>'
                    f'<h3>{esc(r["title"][0])}</h3><p>{esc(r["detail"][0])}</p></div></li>' for r in rows)
    return f'<ul class="risk-list">{items}</ul>'


def build_html(manifest, model, hunks_by_path, out_path, ui_pairs):
    for sid, data in model['sections'].items():  # numbered flow titles are rendered with their index
        if sid == 'flow':
            for i, step in enumerate(data['rows'], 1):
                ui_pairs.setdefault(f'{i}. {step["title"][0]}', f'{i}. {step["title"][1]}')
    present = [s for s in SECTIONS if s[0] == 'changes' or s[0] in model['sections']]
    numbers = {sid: i for i, (sid, _, _) in enumerate(present, 1)}
    nav = '<a href="#conclusion">结论</a>' + ''.join(f'<a href="#{sid}">{esc(label)}</a>' for sid, label, _ in present)
    before, after = [f'<header id="conclusion"><p class="eyebrow">Local code review workspace</p>'
                     f'<h2>{esc(model["conclusion"][0])}</h2></header>'], []
    for sid, _, title in present:
        if sid == 'changes':
            continue
        data = model['sections'][sid]
        (before if numbers[sid] < numbers['changes'] else after).append(
            render_section(sid, numbers[sid], title, data['summary'], section_body(sid, data)))
    t = manifest['totals']
    head = manifest['headSha']
    meta_lines = [f'head {head[:12]}' + (' + working tree' if manifest['worktreeChanges'] else '')]
    commits = manifest['commits']
    meta_lines += [f'includes {c["sha"][:12]}' for c in commits] if len(commits) <= 8 else [f'includes {len(commits)} commits']
    meta_lines += [f'base {manifest["baseRef"]} @ {manifest["mergeBase"][:12]}',
                   f'{plural(t["files"], "file")} · {plural(t["hunks"], "hunk")} · +{t["additions"]} -{t["deletions"]}',
                   f'generated {manifest["generated"]}']
    footer_head = manifest['headSha'][:12] + ('+worktree' if manifest['worktreeChanges'] else '')
    footer = (f'<footer class="footer">基于真实 diff 生成： <code>{esc(manifest["baseRef"])}...{esc(footer_head)}</code>'
              f'。评论仅保存在当前浏览器，不会同步到 GitHub。</footer>')
    guides = {e['path']: {'units': [{'name': u['name'], 'change': u['change'][0], 'reason': u['reason'][0]} for u in e['units']]}
              for d in model['domains'] for g in d['groups'] for e in g['files']}
    guides_en = {e['path']: [[u['change'][1], u['reason'][1]] for u in e['units']]
                 for d in model['domains'] for g in d['groups'] for e in g['files']}
    values = {
        'TITLE': esc(model['title']),
        'HEAD_LABEL': esc(manifest['headLabel']),
        'RAIL_SUBTITLE': esc(model['subtitle']),
        'NAV': nav,
        'RAIL_META': '<br>'.join(esc(line) for line in meta_lines),
        'BEFORE_CHANGES': '\n'.join(before),
        'CHANGES_NUMBER': f'{numbers["changes"]:02d}',
        'CHANGES_SUMMARY': f'<p class="section-summary">{esc(model["changesSummary"][0])}</p>',
        'FILE_GROUPS': render_domains(manifest, model, hunks_by_path, out_path),
        'AFTER_CHANGES': '\n'.join(after + [footer]),
        'BASE_REF_JSON': js_json(manifest['baseRef']),
        'UI_TEXT_EN': js_json(ui_pairs),
        'FILE_DIFF_GUIDES': js_json(guides),
        'FILE_DIFF_GUIDES_EN': js_json(guides_en),
    }
    with open(SHELL_PATH, encoding='utf-8') as handle:
        shell = handle.read()
    tokens = re.findall(r'\{\{([A-Z_]+)\}\}', shell)
    if sorted(tokens) != sorted(values):
        raise Fail(f'assets/review-shell.html tokens {sorted(tokens)} do not match the renderer {sorted(values)}')
    return re.sub(r'\{\{([A-Z_]+)\}\}', lambda m: values[m.group(1)], shell)


# ---------------------------------------------------------------- self-check

class Audit(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
    SKIP_TEXT = {'diff-table', 'diff-file-header', 'file-path', 'ask-panel', 'review-thread', 'language-switch', 'review-comments-note'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.files, self.texts, self.head_label, self.file, self.file_depth = [], [], [], None, None, 0
        self.meta_rows = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        if tag == 'body':
            self.head_label = attrs.get('data-review-commit')
        if tag == 'tr' and 'diff-meta' in classes:
            self.meta_rows += 1
        if tag == 'li' and 'file' in classes:
            self.file = {'status': '', 'add': '', 'del': '', 'path': '', 'hunks': 0, 'additions': 0, 'deletions': 0}
            self.files.append(self.file)
            self.file_depth = len(self.stack)
        if tag == 'tr' and self.file is not None:
            self.file['hunks'] += 'diff-hunk' in classes
            self.file['additions'] += 'addition' in classes
            self.file['deletions'] += 'deletion' in classes
        if tag not in self.VOID:
            self.stack.append((tag, classes))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if self.file is not None and len(self.stack) <= self.file_depth:
            self.file = None

    def handle_data(self, data):
        tags = [t for t, _ in self.stack]
        if 'script' in tags or 'style' in tags:
            return
        classes = set().union(*(c for _, c in self.stack)) if self.stack else set()
        if self.file is not None:
            if 'status-letter' in classes:
                self.file['status'] += data
            elif 'diff-file-header' in classes and tags[-1] == 'a':
                self.file['path'] += data
            elif 'file-summary' in classes and 'added' in classes:
                self.file['add'] += data
            elif 'file-summary' in classes and 'deleted' in classes:
                self.file['del'] += data
        if 'shell' in classes and not classes & self.SKIP_TEXT and data.strip():
            self.texts.append(data.strip())


def audit(html_text, manifest, ui_pairs):
    parser = Audit()
    parser.feed(html_text)
    problems = []
    if parser.head_label != manifest['headLabel']:
        problems.append(f'data-review-commit is {parser.head_label!r}, expected {manifest["headLabel"]!r}')
    if parser.meta_rows:
        problems.append(f'{parser.meta_rows} diff-meta rows rendered; diffs must contain only hunk and code rows')
    rendered = {f['path']: f for f in parser.files}
    if len(parser.files) != len(manifest['files']) or len(rendered) != len(parser.files):
        problems.append(f'rendered {len(parser.files)} file entries ({len(rendered)} unique) for {len(manifest["files"])} changed files')
    for f in manifest['files']:
        r = rendered.get(f['path'])
        if r is None:
            problems.append(f'{f["path"]} is not rendered')
            continue
        if r['status'] != f['status'] or r['hunks'] != f['hunks']:
            problems.append(f'{f["path"]}: rendered status/hunks {r["status"]}/{r["hunks"]}, expected {f["status"]}/{f["hunks"]}')
        if not f['binary'] and (r['add'], r['del'], r['additions'], r['deletions']) != (
                f'+{f["additions"]}', f'-{f["deletions"]}', f['additions'], f['deletions']):
            problems.append(f'{f["path"]}: rendered +/- does not match numstat')
    residue = sorted({t for t in parser.texts if CJK_RE.search(CODE_SPAN_RE.sub('', t)) and
                      (t not in ui_pairs or CJK_RE.search(CODE_SPAN_RE.sub('', ui_pairs[t])))})
    if residue:
        problems.append('Chinese text without an English mapping: ' + ' | '.join(residue[:8]))
    return problems


def cmd_render(args):
    work = os.path.abspath(args.work)
    manifest = read_json(os.path.join(work, 'manifest.json'))
    if manifest.get('schema') != MANIFEST_SCHEMA:
        raise Fail('manifest.json is not from this script version; re-run collect')
    with open(os.path.join(work, 'diff.patch'), encoding='utf-8', newline='') as handle:
        patch = handle.read()
    if sha256(patch) != manifest['diffSha256']:
        raise Fail('diff.patch changed after collect; re-run collect instead of editing it')
    content = read_json(args.content or os.path.join(work, 'content.json'))
    model, checker = validate(content, manifest)
    for warning in checker.warnings:
        print(f'WARNING (fix unless you have a reason): {warning}')
    if checker.errors:
        print(f'content.json has {len(checker.errors)} problem(s); nothing was written:', file=sys.stderr)
        for error in checker.errors:
            print(f'  - {error}', file=sys.stderr)
        return 2
    sections = split_patch(patch)
    _, parsed = assemble_files(parse_name_status_from_manifest(manifest), numstat_from_manifest(manifest), sections)
    hunks_by_path = {f['path']: hunks for f, hunks in zip(manifest['files'], parsed)}
    out_path = os.path.abspath(args.out or manifest['output'])
    ui_pairs = dict(checker.pairs)
    html_text = build_html(manifest, model, hunks_by_path, out_path, ui_pairs)
    problems = audit(html_text, manifest, ui_pairs)
    if problems:
        print('Self-check failed; nothing was written:', file=sys.stderr)
        for problem in problems:
            print(f'  - {problem}', file=sys.stderr)
        return 3
    write_text(out_path, html_text)
    t = manifest['totals']
    groups = sum(len(d['groups']) for d in model['domains'])
    label = manifest['headLabel']
    print(f'Rendered : {out_path}')
    print(f'Checked  : every changed file once, statuses, hunks and +/- match git; {plural(len(model["domains"]), "domain")}, '
          f'{plural(groups, "group")}; zh/en complete')
    print('Report facts (copy into the report):')
    print(f'- Review workspace: {out_path} (file://{urllib.parse.quote(out_path)})')
    print(f'- Scope: {manifest["mode"]}; base {manifest["baseRef"]} @ {manifest["mergeBase"][:12]}; head {label}; '
          f'{plural(len(manifest["commits"]), "commit")}; working-tree changes: {"yes" if manifest["worktreeChanges"] else "no"}')
    print(f'- Size: {plural(t["files"], "file")} · {plural(t["hunks"], "hunk")} · +{t["additions"]} −{t["deletions"]} (matches git)')
    print(f'- Untracked files excluded: {", ".join(manifest["untrackedExcluded"]) or "none"}')
    print(f'- Storage keys: code-review-comments:{label} (comments), code-review-language:{label} (language)')
    return 0


def parse_name_status_from_manifest(manifest):
    return [{'status': f['status'], 'score': f['score'], 'oldPath': f['oldPath'], 'path': f['path']} for f in manifest['files']]


def numstat_from_manifest(manifest):
    return [{'path': f['path'], 'binary': f['binary'], 'additions': f['additions'], 'deletions': f['deletions']}
            for f in manifest['files']]


# ---------------------------------------------------------------- cleanup

def cmd_cleanup(args):
    work = os.path.realpath(args.work)
    temp_root = os.path.realpath(tempfile.gettempdir())
    if not os.path.isfile(os.path.join(work, MARKER)):
        raise Fail(f'{work} was not created by collect (marker missing); not deleting it')
    if not work.startswith(temp_root + os.sep):
        raise Fail(f'{work} is outside {temp_root}; delete it manually if it is no longer needed')

    def make_writable(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    shutil.rmtree(work, onerror=make_writable)
    print(f'Removed  : {work}')
    return 0


# ---------------------------------------------------------------- CLI

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    collect = sub.add_parser('collect', help='resolve the review scope and write the work files')
    collect.add_argument('--repo', default='.', help='repository (or a path inside it); default: current directory')
    collect.add_argument('--base', default='origin/main', help='base ref for local reviews (default: origin/main)')
    scope = collect.add_mutually_exclusive_group()
    scope.add_argument('--head', help='review this commit instead of the working tree')
    scope.add_argument('--pr', help='review a GitHub / GitHub Enterprise pull request URL, diffed against the PR\'s '
                                    'recorded base commit exactly like its "Files changed" tab')
    collect.add_argument('--exclude', action='append', default=[],
                         help='working-tree mode: leave out matching UNTRACKED files (glob, or dir/ prefix); repeatable')
    collect.add_argument('--allow', action='append', default=[],
                         help='working-tree mode: include untracked files that look like secrets (only after the user '
                              'confirms they are safe); repeatable')
    collect.add_argument('--no-fetch', action='store_true', help='do not fetch the base branch before diffing')
    collect.add_argument('--out', help='override the HTML output path')
    collect.add_argument('--work', help='work directory (default: a new temporary directory)')
    collect.set_defaults(func=cmd_collect)

    render = sub.add_parser('render', help='validate content.json and write the HTML')
    render.add_argument('--work', required=True, help='work directory printed by collect')
    render.add_argument('--content', help='content JSON (default: <work>/content.json)')
    render.add_argument('--out', help='override the HTML output path')
    render.set_defaults(func=cmd_render)

    cleanup = sub.add_parser('cleanup', help='delete the temporary work directory')
    cleanup.add_argument('--work', required=True, help='work directory printed by collect')
    cleanup.set_defaults(func=cmd_cleanup)

    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except Fail as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
