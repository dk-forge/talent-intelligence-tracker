<?php
// Offline WordPress dispatch contract with a real SQLite option store.
define('ABSPATH', __DIR__);
define('REST_REQUEST', ($argv[2] ?? '') !== '--internal');
$root = dirname(__DIR__, 2);
$hooks = array(); $uid = 0; $work = 0;
function add_filter($name, $callback, $priority = 10, $args = 1) { $GLOBALS['hooks'][$name][] = $callback; }
function add_action($name, $callback, $priority = 10, $args = 1) { add_filter($name, $callback, $priority, $args); }
function get_current_user_id() { return $GLOBALS['uid']; }
function wp_salt($scheme) { return 'synthetic-test-salt'; }
function register_deactivation_hook($file, $callback) { $GLOBALS['deactivate']=$callback; }
function wp_clear_scheduled_hook($name) { $GLOBALS['cleared']=$name; }
function wp_next_scheduled($name) { return false; }
function wp_schedule_event($time, $schedule, $name) { $GLOBALS['scheduled'] = $name; }
function wp_die($message, $title = '', $args = array()) { $GLOBALS['die_message']=$message; throw new RuntimeException((string) $args['response']); }
class WP_REST_Response {
    public $data, $status, $headers;
    function __construct($data, $status = 200, $headers = array()) { $this->data=$data; $this->status=$status; $this->headers=$headers; }
    function get_status() { return $this->status; }
    function get_headers() { return $this->headers; }
    function get_data() { return $this->data; }
}
class RateDB {
    public $options = 'wp_options', $last_error = '', $broken = false, $write_broken = false;
    public $db, $lock = null, $path;
    function __construct($path) {
        $this->path=$path;
        $this->db=new PDO('sqlite:' . $path);
        $this->db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $this->db->exec('PRAGMA busy_timeout=10000');
        $this->db->exec("CREATE TABLE IF NOT EXISTS wp_options(option_name TEXT PRIMARY KEY, option_value TEXT, autoload TEXT)");
    }
    function prepare($sql, ...$args) { return array($sql, $args); }
    function esc_like($value) { return addcslashes($value, '_%\\'); }
    function get_var($query) {
        [$sql, $args]=$query;
        if (strpos($sql, 'GET_LOCK') !== false) {
            if ($this->broken) return null;
            $this->lock=fopen($this->path . '.' . $args[0] . '.lock', 'c');
            return flock($this->lock, LOCK_EX) ? '1' : '0';
        }
        if (strpos($sql, 'RELEASE_LOCK') !== false) {
            flock($this->lock, LOCK_UN); fclose($this->lock); $this->lock=null; return '1';
        }
        $statement=$this->db->prepare(str_replace(array('%s','%d'), '?', $sql));
        $statement->execute($args); $value=$statement->fetchColumn(); return $value === false ? null : $value;
    }
    function query($query) {
        if ($this->write_broken) return false;
        [$sql, $args]=$query;
        $sql=preg_replace('/ ON DUPLICATE KEY UPDATE.*$/s', " ON CONFLICT(option_name) DO UPDATE SET option_value=excluded.option_value, autoload='no'", $sql);
        $sql=str_replace('LIKE %s', "LIKE %s ESCAPE '\\'", $sql);
        $statement=$this->db->prepare(str_replace(array('%s','%d'), '?', $sql));
        $statement->execute($args); return $statement->rowCount();
    }
}
$wpdb=new RateDB($argv[1]);
$module=$root . '/wordpress-plugin/talent-intelligence-tracker/includes/public-rate-limits.php';
if (is_readable($module)) require $module;
$operations=json_decode(stream_get_contents(STDIN), true); $results=array();
foreach ($operations as $op) {
    $uid=$op['uid'] ?? 0; $_SERVER['REMOTE_ADDR']=$op['ip'] ?? '192.0.2.1';
    $_SERVER['HTTP_USER_AGENT']=$op['agent'] ?? 'synthetic-agent';
    $_SERVER['HTTP_X_FORWARDED_FOR']=$op['forwarded'] ?? '198.51.100.1';
    unset($_SERVER['HTTP_CF_CONNECTING_IP']); if (isset($op['cf'])) $_SERVER['HTTP_CF_CONNECTING_IP']=$op['cf'];
    $wpdb->broken=$op['broken'] ?? false; $wpdb->write_broken=$op['write_broken'] ?? false;
    if (($op['mode'] ?? '') === 'spend') {
        $results[]=function_exists('tit_public_limit_spend') ? tit_public_limit_spend($op['identity'], 2, $op['now']) : 0;
        continue;
    }
    if (($op['mode'] ?? '') === 'deactivate') {
        if (isset($GLOBALS['deactivate'])) ($GLOBALS['deactivate'])();
        continue;
    }
    if (($op['mode'] ?? '') === 'cleanup') {
        foreach ($hooks['tit_public_limit_cleanup'] ?? array() as $callback) $callback();
        continue;
    }
    unset($GLOBALS['tit_public_http_request']);
    $request=new stdClass();
    foreach ($hooks['rest_pre_dispatch'] ?? array() as $callback) $callback(null, null, $request);
    $selected=$request;
    if ($op['nested'] ?? false) {
        $selected=new stdClass();
        foreach ($hooks['rest_pre_dispatch'] ?? array() as $callback) $callback(null, null, $selected);
    }
    $before=$work; $response=null;
    if (($op['mode'] ?? '') === 'form') {
        try {
            foreach ($hooks['admin_post_nopriv_' . $op['action']] ?? array() as $callback) $callback();
        } catch (RuntimeException $error) { $response=new WP_REST_Response(array('message'=>$GLOBALS['die_message']), (int)$error->getMessage()); }
    } else {
        $handler=array('permission_callback'=>($op['private'] ?? false) ? 'tit_api_permission' : '__return_true');
        $response=isset($op['prior']) ? new WP_REST_Response(array(), $op['prior']) : null;
        foreach ($hooks['rest_dispatch_request'] ?? array() as $callback) {
            $response=$callback($response, $selected, $op['route'] ?? '/talent/v1/query', $handler);
        }
    }
    if ($response === null) { $work++; $response=new WP_REST_Response(array('ok'=>true)); }
    $results[]=array('status'=>$response->status,'data'=>$response->data,'headers'=>$response->headers,'work'=>$work-$before);
}
$stored=$wpdb->db->query('SELECT option_name,option_value,autoload FROM wp_options')->fetchAll(PDO::FETCH_ASSOC);
echo json_encode(array('results'=>$results, 'stored'=>$stored, 'locked'=>$wpdb->lock !== null, 'cleared'=>$GLOBALS['cleared'] ?? null));
