import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, 'code-review-workspace', 'scripts')
SCRIPT_PATH = os.path.join(SCRIPTS_DIR, 'review_workspace.py')
sys.path.insert(0, SCRIPTS_DIR)
import review_workspace as rw  # noqa: E402


def git(cwd, *args):
    proc = subprocess.run(['git', '-C', cwd, *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def init_sample_repo(path, remote_url=None):
    os.makedirs(path, exist_ok=True)
    git(path, 'init', '-b', 'main')
    git(path, 'config', 'user.email', 'test@example.com')
    git(path, 'config', 'user.name', 'Test User')
    if remote_url:
        git(path, 'remote', 'add', 'origin', remote_url)
    os.makedirs(os.path.join(path, 'src'), exist_ok=True)
    with open(os.path.join(path, 'src', 'Alpha.ts'), 'w', encoding='utf-8') as f:
        f.write('export const alpha = () => 1;\n')
    with open(os.path.join(path, 'src', 'Alpha.test.ts'), 'w', encoding='utf-8') as f:
        f.write("import { alpha } from './Alpha';\ntest('a', () => expect(alpha()).toBe(1));\n")
    with open(os.path.join(path, 'src', 'Beta.ts'), 'w', encoding='utf-8') as f:
        f.write('export const beta = () => 2;\n')
    with open(os.path.join(path, 'src', 'Gamma.ts'), 'w', encoding='utf-8') as f:
        f.write('export const gamma = () => 3;\n')
    with open(os.path.join(path, 'src', 'Orphan.test.ts'), 'w', encoding='utf-8') as f:
        f.write("test('orphan', () => {});\n")
    git(path, 'add', '.')
    git(path, 'commit', '-m', 'init')
    return git(path, 'rev-parse', 'HEAD')


class ReviewWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='crw-test-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_json_error_shows_caret_and_quote_hint(self):
        bad_path = os.path.join(self.tmp, 'bad.json')
        with open(bad_path, 'w', encoding='utf-8') as f:
            f.write('{\n  "zh": "让前端区分"无权限"和"未知权限""\n}\n')
        with self.assertRaises(rw.Fail) as ctx:
            rw.read_json(bad_path)
        msg = str(ctx.exception)
        self.assertIn('^', msg)
        self.assertIn('unescaped', msg)

    def test_collect_scaffold_check_assemble_render_smoke_and_cleanup(self):
        repo = os.path.join(self.tmp, 'sample-repo')
        base_sha = init_sample_repo(repo)
        git(repo, 'update-ref', 'refs/remotes/origin/main', base_sha)
        git(repo, 'checkout', '-b', 'feat/PROJ-12-sample')
        with open(os.path.join(repo, 'src', 'Alpha.ts'), 'w', encoding='utf-8') as f:
            f.write('export const alpha = () => 10;\n')
        with open(os.path.join(repo, 'src', 'Alpha.test.ts'), 'w', encoding='utf-8') as f:
            f.write("import { alpha } from './Alpha';\ntest('a', () => expect(alpha()).toBe(10));\n")
        with open(os.path.join(repo, 'src', 'Beta.ts'), 'w', encoding='utf-8') as f:
            f.write('export const beta = () => 20;\n')
        with open(os.path.join(repo, 'src', 'Gamma.ts'), 'w', encoding='utf-8') as f:
            f.write('export const gamma = () => 30;\n')
        with open(os.path.join(repo, 'src', 'Orphan.test.ts'), 'w', encoding='utf-8') as f:
            f.write("test('orphan', () => expect(true).toBe(true));\n")
        git(repo, 'add', '.')
        git(repo, 'commit', '-m', 'PROJ-12 update modules')

        work = os.path.join(self.tmp, 'work')
        proc = subprocess.run([sys.executable, SCRIPT_PATH, 'collect', '--repo', repo, '--no-fetch', '--work', work],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest = rw.read_json(os.path.join(work, 'manifest.json'))
        self.assertEqual(len(manifest['files']), 5)
        with open(os.path.join(work, 'diff.patch'), encoding='utf-8') as f:
            patch_lines = f.read().splitlines()
        for fentry in manifest['files']:
            s, e = fentry['patchLines']
            self.assertTrue(patch_lines[s - 1].startswith('diff --git '))
            self.assertIn(fentry['path'], patch_lines[s - 1])
            self.assertLessEqual(s, e)

        # Invalid plan (missing Gamma.ts) must fail upfront in scaffold
        bad_plan = os.path.join(work, 'bad_plan.json')
        rw.write_text(bad_plan, json.dumps({
            'domains': [{'name': 'Frontend', 'groups': [{'files': ['src/Alpha.ts']}]}],
        }))
        res_bad = subprocess.run([sys.executable, SCRIPT_PATH, 'scaffold', '--work', work, '--plan', bad_plan, '--auto-place-tests'],
                                 capture_output=True, text=True)
        self.assertEqual(res_bad.returncode, 2)
        self.assertIn('src/Gamma.ts', res_bad.stderr)

        # Valid plan with subgroups + --auto-place-tests + --max-files 2
        plan_path = os.path.join(work, 'plan.json')
        rw.write_text(plan_path, json.dumps({
            'schema': 'code-review-workspace/plan@1',
            'meta': {'title': 'Code Review · PROJ-12 sample', 'subtitle': 'PROJ-12 · sample'},
            'flow': True,
            'domains': [{
                'name': 'Frontend',
                'groups': [{
                    'subgroups': [
                        {'files': ['src/Alpha.ts']},
                        {'files': ['src/Beta.ts', 'src/Gamma.ts']},
                    ],
                }],
            }],
        }))
        res_ok = subprocess.run([sys.executable, SCRIPT_PATH, 'scaffold', '--work', work, '--plan', plan_path,
                                 '--auto-place-tests', '--max-files', '2'], capture_output=True, text=True)
        self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
        parts_dir = os.path.join(work, 'parts')
        part_names = sorted(os.listdir(parts_dir))
        self.assertEqual(part_names, ['00-overview.json', '01-frontend-01.json', '01-frontend-02.json', '02-tests.json'])

        # Fill parts cleanly via json.dumps
        ov = rw.read_json(os.path.join(parts_dir, '00-overview.json'))
        ov['conclusion'] = {'zh': '前端三个模块升级返回值并同步更新测试', 'en': 'Three frontend modules update return values and tests'}
        ov['behavior'] = {'rows': [{'scenario': {'zh': '返回值', 'en': 'Return values'},
                                    'before': {'zh': '返回 1/2/3', 'en': 'Returns 1/2/3'},
                                    'after': {'zh': '返回 10/20/30', 'en': 'Returns 10/20/30'}}]}
        ov['flow'] = {'steps': [
            {'title': {'zh': '第一步', 'en': 'Step one'}, 'detail': {'zh': '更新 Alpha', 'en': 'Update Alpha'}},
            {'title': {'zh': '第二步', 'en': 'Step two'}, 'detail': {'zh': '更新 Beta 与 Gamma', 'en': 'Update Beta and Gamma'}},
        ]}
        ov['changesSummary'] = {'zh': '更新三个模块与独立测试', 'en': 'Update three modules and an unpaired test'}
        ov['risks'] = {'items': [{'level': 'Low', 'title': {'zh': '常量变更', 'en': 'Constant change'},
                                  'detail': {'zh': '调用方需适配新值', 'en': 'Callers must expect new values'}}]}
        rw.write_text(os.path.join(parts_dir, '00-overview.json'), json.dumps(ov, ensure_ascii=False, indent=2))

        p1 = rw.read_json(os.path.join(parts_dir, '01-frontend-01.json'))
        p1['points'] = [{'zh': '模块返回值放大十倍', 'en': 'Scale module outputs by ten'}]
        p1['groups'][0]['title'] = {'zh': '核心模块调整', 'en': 'Core module updates'}
        p1['groups'][0]['summary'] = {'zh': '分两步更新导出常量', 'en': 'Update exported constants in two steps'}
        p1['groups'][0]['subgroups'][0]['title'] = {'zh': 'Alpha 与测试', 'en': 'Alpha and test'}
        p1['groups'][0]['subgroups'][0]['summary'] = {'zh': '更新 Alpha 返回值及对应单测', 'en': 'Update Alpha output and unit test'}
        p1['groups'][0]['subgroups'][0]['files'][0]['role'] = {'zh': 'Alpha 改为返回 10', 'en': 'Alpha returns 10'}
        p1['groups'][0]['subgroups'][0]['files'][0]['units'] = [{
            'name': 'alpha',
            'change': {'zh': '返回值从 1 改为 10。', 'en': 'Return value changes from 1 to 10.'},
            'reason': {'zh': '调整「输出基数」到十位。', 'en': 'Adjusts output base to tens.'},
        }]
        p1['groups'][0]['subgroups'][0]['files'][1]['role'] = {'zh': '验证 Alpha 返回 10', 'en': 'Verify Alpha returns 10'}
        p1['groups'][0]['subgroups'][0]['files'][1]['units'] = [{
            'name': "test('a')",
            'change': {'zh': '断言从 1 更新为 10。', 'en': 'Assertion updated from 1 to 10.'},
            'reason': {'zh': '同步实现修改。', 'en': 'Matches implementation change.'},
        }]
        rw.write_text(os.path.join(parts_dir, '01-frontend-01.json'), json.dumps(p1, ensure_ascii=False, indent=2))

        # Editing protection on scaffold re-run without --force
        res_prot = subprocess.run([sys.executable, SCRIPT_PATH, 'scaffold', '--work', work, '--plan', plan_path, '--auto-place-tests'],
                                  capture_output=True, text=True)
        self.assertEqual(res_prot.returncode, 1)
        self.assertIn('--force', res_prot.stderr)

        # check --part succeeds on p1
        res_chk = subprocess.run([sys.executable, SCRIPT_PATH, 'check', '--work', work, '--part', os.path.join(parts_dir, '01-frontend-01.json')],
                                 capture_output=True, text=True)
        self.assertEqual(res_chk.returncode, 0, res_chk.stderr)

        p2 = rw.read_json(os.path.join(parts_dir, '01-frontend-02.json'))
        p2['groups'][0]['subgroups'][0]['title'] = {'zh': 'Beta 与 Gamma', 'en': 'Beta and Gamma'}
        p2['groups'][0]['subgroups'][0]['summary'] = {'zh': '更新剩余两个导出函数', 'en': 'Update remaining two exports'}
        for idx, name in enumerate(('beta', 'gamma')):
            val = 20 if idx == 0 else 30
            p2['groups'][0]['subgroups'][0]['files'][idx]['role'] = {'zh': f'{name} 改为返回 {val}', 'en': f'{name} returns {val}'}
            p2['groups'][0]['subgroups'][0]['files'][idx]['units'] = [{
                'name': name,
                'change': {'zh': f'返回值改为 {val}。', 'en': f'Return value changes to {val}.'},
                'reason': {'zh': '保持一致的十倍基数。', 'en': 'Keeps consistent ten-fold scale.'},
            }]
        rw.write_text(os.path.join(parts_dir, '01-frontend-02.json'), json.dumps(p2, ensure_ascii=False, indent=2))

        pt = rw.read_json(os.path.join(parts_dir, '02-tests.json'))
        pt['points'] = [{'zh': '补充独立测试断言', 'en': 'Add assertion to orphan test'}]
        pt['groups'][0]['title'] = {'zh': '独立测试', 'en': 'Unpaired tests'}
        pt['groups'][0]['summary'] = {'zh': '为孤立测试补充真实断言', 'en': 'Add an assertion to the standalone test'}
        pt['groups'][0]['files'][0]['role'] = {'zh': '增加布尔断言', 'en': 'Add boolean assertion'}
        pt['groups'][0]['files'][0]['units'] = [{
            'name': "test('orphan')",
            'change': {'zh': '空测试体新增 `expect(true).toBe(true)`。', 'en': 'Empty test body gains `expect(true).toBe(true)`.'},
            'reason': {'zh': '避免空测试静默通过。', 'en': 'Avoids empty test passing silently.'},
        }]
        rw.write_text(os.path.join(parts_dir, '02-tests.json'), json.dumps(pt, ensure_ascii=False, indent=2))

        # Auto-assemble when running render directly while content.json is still untouched skeleton
        res_render = subprocess.run([sys.executable, SCRIPT_PATH, 'render', '--work', work], capture_output=True, text=True)
        self.assertEqual(res_render.returncode, 0, res_render.stderr)
        self.assertIn('Assembled:', res_render.stdout)
        self.assertIn('Rendered :', res_render.stdout)
        self.assertTrue(os.path.isfile(manifest['output']))

        # Warn if parts are newer than content.json on subsequent render
        time.sleep(0.02)
        os.utime(os.path.join(parts_dir, '02-tests.json'), None)
        res_stale = subprocess.run([sys.executable, SCRIPT_PATH, 'render', '--work', work], capture_output=True, text=True)
        self.assertEqual(res_stale.returncode, 0, res_stale.stderr)
        self.assertIn('newer than content.json', res_stale.stdout)

        # Assemble --render directly
        res_asm = subprocess.run([sys.executable, SCRIPT_PATH, 'assemble', '--work', work, '--render'], capture_output=True, text=True)
        self.assertEqual(res_asm.returncode, 0, res_asm.stderr)
        self.assertIn('Rendered :', res_asm.stdout)

        # Run smoke test via smoke subcommand if node is installed and playwright cache/chrome exists
        if shutil.which('node'):
            res_smoke = subprocess.run([sys.executable, SCRIPT_PATH, 'smoke', '--work', work], capture_output=True, text=True)
            self.assertEqual(res_smoke.returncode, 0, res_smoke.stdout + '\n' + res_smoke.stderr)
            self.assertIn('"ok":true', res_smoke.stdout)

        res_clean = subprocess.run([sys.executable, SCRIPT_PATH, 'cleanup', '--work', work], capture_output=True, text=True)
        self.assertEqual(res_clean.returncode, 0, res_clean.stderr)
        self.assertFalse(os.path.exists(work))

    def test_scaffold_rejects_wrong_subgroup_and_check_catches_evidence(self):
        repo = os.path.join(self.tmp, 'sub-repo')
        base_sha = init_sample_repo(repo)
        git(repo, 'update-ref', 'refs/remotes/origin/main', base_sha)
        with open(os.path.join(repo, 'src', 'Alpha.ts'), 'w', encoding='utf-8') as f:
            f.write('export const alpha = () => 11;\n')
        with open(os.path.join(repo, 'src', 'Alpha.test.ts'), 'w', encoding='utf-8') as f:
            f.write("import { alpha } from './Alpha';\ntest('a', () => expect(alpha()).toBe(11));\n")
        with open(os.path.join(repo, 'src', 'Beta.ts'), 'w', encoding='utf-8') as f:
            f.write('export const beta = () => 22;\n')
        git(repo, 'add', '.')
        git(repo, 'commit', '-m', 'update alpha and beta')

        work = os.path.join(self.tmp, 'sub-work')
        subprocess.run([sys.executable, SCRIPT_PATH, 'collect', '--repo', repo, '--no-fetch', '--work', work],
                       check=True, capture_output=True)

        # Put Alpha.test.ts in subgroup[1] while Alpha.ts is in subgroup[0]
        wrong_plan = os.path.join(work, 'wrong_plan.json')
        rw.write_text(wrong_plan, json.dumps({
            'domains': [{
                'name': 'Frontend',
                'groups': [{
                    'subgroups': [
                        {'files': ['src/Alpha.ts']},
                        {'files': ['src/Beta.ts', 'src/Alpha.test.ts']},
                    ],
                }],
            }],
        }))
        res_wrong = subprocess.run([sys.executable, SCRIPT_PATH, 'scaffold', '--work', work, '--plan', wrong_plan],
                                   capture_output=True, text=True)
        self.assertEqual(res_wrong.returncode, 2)
        self.assertIn('same subgroup as src/Alpha.ts', res_wrong.stderr)

        # Correct plan -> scaffold -> put evidence citation in part -> check must reject
        right_plan = os.path.join(work, 'right_plan.json')
        rw.write_text(right_plan, json.dumps({
            'domains': [{
                'name': 'Frontend',
                'groups': [{
                    'subgroups': [
                        {'files': ['src/Alpha.ts', 'src/Alpha.test.ts']},
                        {'files': ['src/Beta.ts']},
                    ],
                }],
            }],
        }))
        subprocess.run([sys.executable, SCRIPT_PATH, 'scaffold', '--work', work, '--plan', right_plan],
                       check=True, capture_output=True)
        part_path = os.path.join(work, 'parts', '01-frontend.json')
        p = rw.read_json(part_path)
        p['points'] = [{'zh': '要点', 'en': 'Point'}]
        p['groups'][0]['title'] = {'zh': '组', 'en': 'Group'}
        p['groups'][0]['summary'] = {'zh': '组说明', 'en': 'Group summary'}
        p['groups'][0]['subgroups'][0]['title'] = {'zh': '子组一', 'en': 'Subgroup 1'}
        p['groups'][0]['subgroups'][0]['summary'] = {'zh': '子组一说明', 'en': 'Subgroup 1 summary'}
        p['groups'][0]['subgroups'][0]['files'][0]['role'] = {'zh': '角色', 'en': 'Role'}
        p['groups'][0]['subgroups'][0]['files'][0]['units'] = [{
            'name': 'alpha',
            'change': {'zh': '修改见 Alpha.ts:1。', 'en': 'See Alpha.ts:1.'},
            'reason': {'zh': '原因。', 'en': 'Reason.'},
        }]
        rw.write_text(part_path, json.dumps(p, ensure_ascii=False, indent=2))
        res_ev = subprocess.run([sys.executable, SCRIPT_PATH, 'check', '--work', work, '--part', part_path],
                                capture_output=True, text=True)
        self.assertEqual(res_ev.returncode, 2)
        self.assertIn('looks like an evidence citation', res_ev.stderr)

    def test_ambiguous_named_repo_lists_candidates(self):
        root_a = os.path.join(self.tmp, 'roots', 'a', 'dup-app')
        root_b = os.path.join(self.tmp, 'roots', 'b', 'dup-app')
        init_sample_repo(root_a, 'https://github.com/acme/dup-app.git')
        init_sample_repo(root_b, 'https://github.com/acme/dup-app.git')
        res = subprocess.run(
            [sys.executable, SCRIPT_PATH, 'collect', '--repo', 'dup-app',
             '--search-root', os.path.join(self.tmp, 'roots'), '--no-fetch'],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 1)
        self.assertIn('multiple local repositories matched', res.stderr)
        self.assertIn(os.path.realpath(root_a), res.stderr)
        self.assertIn(os.path.realpath(root_b), res.stderr)

    def test_locate_and_collect_auto_discover_and_fallback(self):
        bare = os.path.join(self.tmp, 'remote', 'acme', 'my-app.git')
        subprocess.run(['git', 'init', '--bare', '-b', 'main', bare], check=True, capture_output=True)

        seed_repo = os.path.join(self.tmp, 'seed_area', 'seed')
        base_sha = init_sample_repo(seed_repo, bare)
        git(seed_repo, 'push', 'origin', 'main')
        git(seed_repo, 'checkout', '-b', 'feat/PROJ-99-pr')
        with open(os.path.join(seed_repo, 'src', 'Alpha.ts'), 'w', encoding='utf-8') as f:
            f.write('export const alpha = () => 99;\n')
        git(seed_repo, 'add', '.')
        git(seed_repo, 'commit', '-m', 'PROJ-99 change alpha')
        head_sha = git(seed_repo, 'rev-parse', 'HEAD')
        git(seed_repo, 'push', 'origin', f'{head_sha}:refs/pull/42/head')

        # Create local clone in search root with matching github URL, plus an analysis workspace repo
        code_root = os.path.join(self.tmp, 'code')
        local_clone = os.path.join(code_root, 'my-app')
        subprocess.run(['git', 'clone', bare, local_clone], check=True, capture_output=True)
        git(local_clone, 'remote', 'set-url', 'origin', 'https://github.com/acme/my-app.git')

        notes_ws = os.path.join(self.tmp, 'ws', 'notes_ws')
        init_sample_repo(notes_ws, 'https://github.com/user/notes.git')
        os.makedirs(os.path.join(notes_ws, 'analysis'), exist_ok=True)

        # 1. locate --name finds local_clone from notes_ws
        res_loc = subprocess.run(
            [sys.executable, SCRIPT_PATH, 'locate', '--name', 'my-app', '--repo', notes_ws, '--search-root', code_root],
            capture_output=True, text=True,
        )
        self.assertEqual(res_loc.returncode, 0, res_loc.stderr)
        self.assertIn(os.path.realpath(local_clone), os.path.realpath(res_loc.stdout))

        # 2. collect --repo my-app (without knowing path) resolves to local_clone
        work1 = os.path.join(self.tmp, 'w1')
        res_named = subprocess.run(
            [sys.executable, SCRIPT_PATH, 'collect', '--repo', 'my-app', '--search-root', code_root, '--no-fetch',
             '--base', 'main', '--work', work1],
            capture_output=True, text=True,
        )
        # Note: working tree is clean on local_clone -> exits 1 with "no changes", but must print Resolved!
        self.assertIn('Resolved  : --repo my-app ->', res_named.stdout)
        self.assertIn('no changes', res_named.stderr)

        # 3. Mock gh CLI and git url.insteadOf for local bare clone fetch
        fake_bin = os.path.join(self.tmp, 'bin')
        os.makedirs(fake_bin)
        gh_path = os.path.join(fake_bin, 'gh')
        with open(gh_path, 'w', encoding='utf-8') as f:
            f.write(f'''#!/usr/bin/env python3
import json, subprocess, sys
args = sys.argv[1:]
if args[:2] == ['pr', 'view']:
    print(json.dumps({{
        "number": 42,
        "title": "PROJ-99 update alpha",
        "body": "",
        "state": "OPEN",
        "baseRefName": "main",
        "baseRefOid": "{base_sha}",
        "headRefName": "feat/PROJ-99-pr",
        "headRefOid": "{head_sha}",
        "url": "https://github.com/acme/my-app/pull/42"
    }}))
    sys.exit(0)
if args[:2] == ['repo', 'clone']:
    dest = args[3]
    subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout', '--quiet', '{bare}', dest], check=True)
    sys.exit(0)
sys.exit(1)
''')
        os.chmod(gh_path, os.stat(gh_path).st_mode | stat.S_IEXEC)

        # Use git config file so https://github.com/acme/my-app.git maps to bare repo
        git_cfg = os.path.join(self.tmp, 'gitconfig')
        with open(git_cfg, 'w', encoding='utf-8') as f:
            f.write(f'[url "file://{bare}"]\n\tinsteadOf = https://github.com/acme/my-app.git\n')
        env = dict(os.environ, PATH=fake_bin + os.pathsep + os.environ['PATH'], GIT_CONFIG_GLOBAL=git_cfg)

        work2 = os.path.join(self.tmp, 'w2')
        res_pr = subprocess.run(
            [sys.executable, SCRIPT_PATH, 'collect', '--pr', 'https://github.com/acme/my-app/pull/42',
             '--repo', notes_ws, '--search-root', code_root, '--work', work2],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(res_pr.returncode, 0, res_pr.stderr)
        m2 = rw.read_json(os.path.join(work2, 'manifest.json'))
        self.assertFalse(m2['clonedRepo'], m2['notes'])
        self.assertTrue(m2['discoveredRepo'], m2)
        self.assertEqual(os.path.realpath(m2['repoRoot']), os.path.realpath(local_clone))
        self.assertTrue(os.path.realpath(m2['output']).startswith(os.path.realpath(notes_ws) + os.sep))

        # 4. If discovered local clone fails fetch, PR collect falls back to temporary clone cleanly
        broken_cfg = os.path.join(self.tmp, 'broken_gitconfig')
        with open(broken_cfg, 'w', encoding='utf-8') as f:
            f.write('[url "file:///nonexistent/acme/my-app.git"]\n\tinsteadOf = https://github.com/acme/my-app.git\n')
        env_broken = dict(os.environ, PATH=fake_bin + os.pathsep + os.environ['PATH'], GIT_CONFIG_GLOBAL=broken_cfg)
        work3 = os.path.join(self.tmp, 'w3')
        res_fallback = subprocess.run(
            [sys.executable, SCRIPT_PATH, 'collect', '--pr', 'https://github.com/acme/my-app/pull/42',
             '--repo', notes_ws, '--search-root', code_root, '--work', work3],
            capture_output=True, text=True, env=env_broken,
        )
        self.assertEqual(res_fallback.returncode, 0, res_fallback.stderr)
        m3 = rw.read_json(os.path.join(work3, 'manifest.json'))
        self.assertTrue(m3['clonedRepo'])
        self.assertFalse(m3['discoveredRepo'])
        self.assertTrue(any('used a temporary clone instead' in n for n in m3['notes']))


if __name__ == '__main__':
    unittest.main()
