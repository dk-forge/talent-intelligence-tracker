"""Public callbacks spend shared origin quotas before doing service work."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'wordpress-plugin/talent-intelligence-tracker'
HARNESS = ROOT / 'tests/php/public_rate_limits.php'
PHP = os.environ.get('PHP_BINARY') or shutil.which('php')


class PublicLimits(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PHP, 'PHP is required to verify the rate-limit contract')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / 'options.sqlite')

    def run_ops(self, operations, internal=False):
        result = subprocess.run([PHP, str(HARNESS), self.db] + (['--internal'] if internal else []), input=json.dumps(operations),
                                text=True, capture_output=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def test_shared_ip_quota_stops_callbacks_despite_route_agent_and_header_rotation(self):
        ops = [dict(route='/talent/v1/' + ('query' if i % 2 else 'facets'),
                    agent=f'synthetic-agent-{i}', forwarded=f'198.51.100.{i % 250}') for i in range(121)]
        result = self.run_ops(ops)
        self.assertEqual(sum(r['work'] for r in result['results']), 120)
        last = result['results'][-1]
        self.assertEqual(last['status'], 429)
        self.assertGreater(int(last['headers']['Retry-After']), 0)
        self.assertIn('no-store', last['headers']['Cache-Control'])
        self.assertNotIn('192.0.2.1', json.dumps(result['stored']))
        self.assertNotIn('synthetic-agent', json.dumps(result['stored']))
        self.assertTrue(all(row['autoload'] == 'no' for row in result['stored']))

    def test_cloudflare_proxy_peer_gives_each_visitor_their_own_quota(self):
        edge = '172.70.1.1'  # inside a published Cloudflare range
        ops = [dict(ip=edge, cf='203.0.113.7', forwarded='203.0.113.7')] * 121
        ops += [dict(ip=edge, cf='203.0.113.8', forwarded='203.0.113.8')] * 5
        result = self.run_ops(ops)['results']
        self.assertEqual(result[120]['status'], 429)
        self.assertEqual([r['status'] for r in result[121:]], [200] * 5)

    def test_internal_hop_uses_forwarded_chain_right_to_left(self):
        hop = '100.64.0.9'  # private proxy hop in front of the origin
        ops = [dict(ip=hop, forwarded='198.51.100.50, 172.70.1.1')] * 121
        ops += [dict(ip=hop, forwarded='evil-spoof, 198.51.100.51, 172.70.1.1')] * 3
        ops += [dict(ip=hop, forwarded='9.9.9.9, 198.51.100.50, 172.70.1.1')] * 1  # forged left entry ignored
        result = self.run_ops(ops)['results']
        self.assertEqual(result[120]['status'], 429)
        self.assertEqual([r['status'] for r in result[121:124]], [200] * 3)
        self.assertEqual(result[124]['status'], 429)

    def test_forged_headers_from_a_non_proxy_peer_are_ignored(self):
        ops = [dict(ip='192.0.2.55', cf=f'203.0.113.{i % 250}', forwarded=f'198.51.100.{i % 250}, 172.70.1.1')
               for i in range(121)]
        result = self.run_ops(ops)['results']
        self.assertEqual(sum(r['work'] for r in result), 120)
        self.assertEqual(result[-1]['status'], 429)

    def test_unknown_client_ip_fails_open_instead_of_sharing_one_key(self):
        ops = [dict(ip='10.0.0.5', forwarded='')] * 130              # proxy hop, no client info
        ops += [dict(ip='100.64.0.9', forwarded='not-an-ip')] * 130  # garbage chain
        ops += [dict(ip='unparseable', forwarded='198.51.100.1')] * 130
        result = self.run_ops(ops)
        self.assertTrue(all(r['status'] == 200 for r in result['results']))
        self.assertEqual(result['stored'], [])

    def test_per_user_quota_survives_peer_rotation(self):
        result = self.run_ops([dict(uid=7, ip=f'192.0.2.{i+1}') for i in range(61)])
        self.assertEqual(sum(r['work'] for r in result['results']), 60)
        self.assertEqual(result['results'][-1]['status'], 429)

    def test_distinct_ips_are_independent_and_private_routes_keep_their_permissions(self):
        ops = [{}] * 120 + [dict(ip='192.0.2.2'), dict(private=True), dict(route='/other/v1/query'), dict(prior=403)]
        result = self.run_ops(ops)['results']
        self.assertEqual([r['status'] for r in result[-4:]], [200, 200, 200, 403])

    def test_exact_window_boundary_resets_the_counter(self):
        result = self.run_ops([dict(mode='spend', identity='synthetic', now=now)
                               for now in (10000, 10000, 10059, 10060)])
        self.assertEqual(result['results'], [0, 0, 1, 0])
        self.assertFalse(result['locked'])

    def test_storage_failure_stops_work_and_releases_locks(self):
        result = self.run_ops([dict(broken=True), dict(write_broken=True), {}])
        self.assertEqual([r['status'] for r in result['results']], [503, 503, 200])
        self.assertEqual([r['work'] for r in result['results']], [0, 0, 1])
        self.assertFalse(result['locked'])

    def test_multiple_workers_share_one_atomic_quota(self):
        self.run_ops([])
        with ThreadPoolExecutor(max_workers=8) as pool:
            batches = list(pool.map(lambda _: self.run_ops([{}] * 10), range(16)))
        self.assertEqual(sum(r['work'] for batch in batches for r in batch['results']), 120)

    def test_public_download_and_form_hooks_also_stop_before_work(self):
        for action in ['tit_export_csv', 'tit_export_json', 'tit_export_hubspot', 'tit_export_salesforce']:
            with self.subTest(action=action):
                self.db = str(Path(self.temp.name) / (action + '.sqlite'))
                result = self.run_ops([dict(mode='form', action=action)] * 121)['results']
                self.assertEqual(sum(r['work'] for r in result), 120)
                self.assertEqual(result[-1]['status'], 429)

    def test_expired_options_are_cleaned_without_dropping_live_limits(self):
        result = self.run_ops([dict(mode='spend', identity='expired', now=1), {}, dict(mode='cleanup')])
        self.assertEqual(len(result['stored']), 1)

    def test_plugin_loads_the_guard_and_public_permissions_remain_audited(self):
        self.assertIn('includes/public-rate-limits.php', (SITE / 'talent-intelligence-tracker.php').read_text())
        values = set()
        for path in SITE.rglob('*.php'):
            values.update(re.findall(r"['\"]permission_callback['\"]\s*=>\s*([^,\n]+)", path.read_text()))
        self.assertTrue(any("'__return_true'" in value for value in values))
        for value in values:
            self.assertTrue(any(allowed in value for allowed in ("'__return_true'", "'tit_api_permission'", "$keyed")), value)

    def test_deactivation_clears_the_event_and_private_counter_rows(self):
        result = self.run_ops([{}, dict(mode='deactivate')])
        self.assertEqual(result['stored'], [])
        self.assertEqual(result['cleared'], 'tit_public_limit_cleanup')

    def test_every_anonymous_action_has_a_guard_or_a_signed_consent_contract(self):
        actions = set()
        for path in SITE.rglob('*.php'):
            source = path.read_text()
            actions.update(re.findall(r"add_action\(['\"]admin_post_nopriv_([^'\"]+)", source))
            self.assertGreaterEqual(source.count("'permission_callback'"),
                                    len(re.findall(r'\bregister_rest_route\s*\(', source)), str(path))
        self.assertEqual(actions, set(['tit_export_csv', 'tit_export_json', 'tit_export_hubspot', 'tit_export_salesforce']) | set())

    def test_form_refusal_retains_a_visible_tracked_match_cta(self):
        result = self.run_ops([dict(mode='form', action='tit_export_csv')] * 121)['results'][-1]
        self.assertEqual(result['status'], 429)
        message = result['data']['message']
        self.assertIn('Check my match, free</a>', message)
        self.assertIn('https://asktherecruiter.com/resume-score?utm_source=talent_tracker', message)
        for tag in ('utm_source=', 'utm_medium=', 'utm_campaign='):
            self.assertIn(tag, message)

    def test_internal_digest_and_nested_dispatches_do_not_spend_public_http_quota(self):
        internal = self.run_ops([{}] * 130, internal=True)
        self.assertTrue(all(r['status'] == 200 for r in internal['results']))
        self.assertEqual(internal['stored'], [])
        nested = self.run_ops([dict(nested=True)] * 130)
        self.assertTrue(all(r['status'] == 200 for r in nested['results']))
        self.assertEqual(nested['stored'], [])
        public = self.run_ops([{}] * 121)
        self.assertEqual(public['results'][-1]['status'], 429)

    def test_apache_error_responses_cannot_receive_shared_cache_headers(self):
        bootstrap = """define('ABSPATH', '/tmp/');
            define('TIT_URL', '/blog/wp-content/plugins/talent-intelligence-tracker/');
            function add_action(...$args) {}
            function rest_url($path) { return '/blog/wp-json/' . $path; }
            function home_url($path) { return '/blog' . $path; }
            function wp_parse_url($url, $part) { return parse_url($url, $part); }
        """
        config = subprocess.run([PHP, '-r', bootstrap + 'require $argv[1]; '
                                 + 'echo json_encode(tit_htaccess_block_lines());',
                                 str(SITE / 'includes/htaccess.php')],
                                check=True, text=True, capture_output=True)
        blocks = []
        current = None
        for line in json.loads(config.stdout):
            if line.startswith('<If '):
                current = []
            elif line == '</If>':
                blocks.append(current)
                current = None
            elif current is not None and line.startswith('Header '):
                current.append(line)
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            with self.subTest(block=block):
                # Late mod_headers expressions see the final response status.
                # Clear the normal table too, so public and private cannot coexist.
                self.assertEqual(block[-2:], [
                    'Header unset Cache-Control "expr=%{REQUEST_STATUS} >= 400"',
                    'Header always set Cache-Control "no-store, private" '
                    '"expr=%{REQUEST_STATUS} >= 400"'])
                self.assertTrue(any(line.startswith('Header set Cache-Control "public,')
                                    for line in block))
