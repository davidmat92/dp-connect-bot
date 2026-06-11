<?php
/**
 * Plugin Name: DP Bot Deploy
 * Description: Sicherer Empfaenger fuer automatisches mu-plugin Deployment via deploy.sh. Auth ueber WordPress Application Passwords (Benutzer braucht update_plugins-Recht).
 * Version: 1.0
 */

if (!defined('ABSPATH')) exit;

add_action('rest_api_init', function () {
    register_rest_route('dpbot/v1', '/deploy-plugin', [
        'methods'             => 'POST',
        'permission_callback' => function () {
            return current_user_can('update_plugins');
        },
        'callback'            => 'dpbd_handle_deploy',
    ]);
});

function dpbd_handle_deploy(WP_REST_Request $req) {
    $whitelist = ['dp-connect-chat.php', 'dp-bot-admin.php', 'dp-bot-deploy.php'];

    $filename    = basename((string) $req->get_param('filename'));
    $content_b64 = (string) $req->get_param('content');
    $checksum    = strtolower((string) $req->get_param('sha256'));

    if (!in_array($filename, $whitelist, true)) {
        return new WP_Error('dpbd_forbidden_file', 'Datei nicht in Whitelist: ' . $filename, ['status' => 400]);
    }

    $content = base64_decode($content_b64, true);
    if ($content === false || $content === '') {
        return new WP_Error('dpbd_bad_content', 'Ungueltiger Base64-Content', ['status' => 400]);
    }
    if (!hash_equals(hash('sha256', $content), $checksum)) {
        return new WP_Error('dpbd_checksum', 'SHA256-Checksumme stimmt nicht', ['status' => 400]);
    }
    if (strpos($content, '<?php') !== 0) {
        return new WP_Error('dpbd_not_php', 'Content beginnt nicht mit <?php', ['status' => 400]);
    }

    $target = WPMU_PLUGIN_DIR . '/' . $filename;
    $backup = $target . '.bak';

    $had_backup = false;
    if (file_exists($target)) {
        if (!copy($target, $backup)) {
            return new WP_Error('dpbd_backup_failed', 'Backup fehlgeschlagen', ['status' => 500]);
        }
        $had_backup = true;
    }

    // Atomar schreiben: erst .tmp, dann rename
    $tmp = $target . '.tmp';
    if (file_put_contents($tmp, $content) === false || !rename($tmp, $target)) {
        @unlink($tmp);
        return new WP_Error('dpbd_write_failed', 'Schreiben fehlgeschlagen (Dateirechte?)', ['status' => 500]);
    }

    // Loopback-Check: mu-plugins laufen bei JEDEM Request – wenn die neue Datei
    // die Site crasht (Fatal Error), liefert dieser Check 5xx und wir rollbacken.
    // Der aktuelle Request laeuft noch mit dem alten Code im Speicher.
    $check = wp_remote_get(home_url('/'), ['timeout' => 20, 'sslverify' => false]);
    $code  = is_wp_error($check) ? 0 : wp_remote_retrieve_response_code($check);
    if ($code === 0 || $code >= 500) {
        if ($had_backup) {
            copy($backup, $target);
        } else {
            @unlink($target);
        }
        return new WP_Error(
            'dpbd_site_broken',
            sprintf('Site-Check nach Deploy fehlgeschlagen (HTTP %d) - Rollback durchgefuehrt', $code),
            ['status' => 500]
        );
    }

    return rest_ensure_response([
        'ok'         => true,
        'file'       => $filename,
        'bytes'      => strlen($content),
        'site_check' => $code,
    ]);
}
