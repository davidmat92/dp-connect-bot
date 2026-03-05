/**
 * =========================================================================
 * BOT-TRIGGERED PASSWORD RESET – REST API Endpoint
 * =========================================================================
 * Diesen Code VOR dem "if (is_admin())" Block im Snippet einfuegen.
 * Ermoeglicht dem Telegram-Bot, ein neues Passwort fuer einen Kunden
 * zu generieren und per CI-Mail zu versenden.
 */
const DP_BOT_SECRET = 'dp_b0t_r3s3t_2024_xK9mP';

/**
 * HTML Mail – Neues Passwort (identisch mit Admin-Button-Mail)
 * Muss ausserhalb von is_admin() definiert sein, damit REST API darauf zugreifen kann.
 */
function dp_bot_send_new_password_mail($to, $username, $password, $nachname) {
    $greeting = !empty($nachname)
        ? 'Hallo ' . esc_html($nachname) . ','
        : 'Sehr geehrte Damen und Herren,';

    $subject   = 'Ihr neues Passwort für DP Connect';
    $login_url = DP_LOGIN_URL;
    $logo_url  = 'https://dpconnect.de/wp-content/uploads/2026/01/logo_dpconnect.webp';

    $html = '<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; background-color: #F5F5F5; font-family: Helvetica Neue, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">

    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #F5F5F5;">
    <tr><td align="center" style="padding: 40px 20px;">

    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #FFFFFF; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); overflow: hidden;">

    <!-- Logo -->
    <tr><td style="text-align: center; padding: 32px 0 12px 0; background-color: #FFFFFF;">
        <a href="https://dpconnect.de" style="text-decoration: none;">
            <img src="' . esc_url($logo_url) . '" alt="DP Connect" width="100" height="100" style="display: inline-block; max-width: 100px; height: auto; border: 0;" />
        </a>
    </td></tr>

    <!-- Header -->
    <tr><td style="background-color: #FFFFFF; border-bottom: 3px solid #F68622; text-align: center; padding: 20px 48px;">
        <h1 style="color: #1A1A1A; font-family: Helvetica Neue, Helvetica, Arial, sans-serif; font-size: 18px; font-weight: 600; letter-spacing: 0.3px; margin: 0;">
            Ihr neues Passwort
        </h1>
    </td></tr>

    <!-- Body -->
    <tr><td style="background-color: #FFFFFF; padding: 32px 48px;">

        <p style="color: #1A1A1A; font-size: 13px; line-height: 1.7; margin: 0 0 18px 0;">' . $greeting . '</p>

        <p style="color: #1A1A1A; font-size: 13px; line-height: 1.7; margin: 0 0 24px 0;">
            f&uuml;r Ihr Kundenkonto bei <strong style="color: #F68622;">DP Connect</strong> wurde ein neues Passwort vergeben.
        </p>

        <!-- Zugangsdaten Box -->
        <table border="0" cellpadding="0" cellspacing="0" width="100%" style="margin-bottom: 24px;">
        <tr><td style="background-color: #F5F5F5; border: 1px solid #E0E0E0; border-radius: 8px; padding: 20px 24px;">
            <p style="margin: 0 0 12px 0; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #999; font-weight: 600;">Ihre neuen Zugangsdaten</p>
            <table border="0" cellpadding="0" cellspacing="0">
                <tr>
                    <td style="padding: 4px 16px 4px 0; font-size: 13px; color: #777;">Benutzer:</td>
                    <td style="padding: 4px 0; font-size: 13px; color: #1A1A1A; font-weight: 600;">' . esc_html($username) . '</td>
                </tr>
                <tr>
                    <td style="padding: 4px 16px 4px 0; font-size: 13px; color: #777;">Neues Passwort:</td>
                    <td style="padding: 4px 0; font-size: 13px; color: #1A1A1A; font-weight: 600;">' . esc_html($password) . '</td>
                </tr>
            </table>
        </td></tr>
        </table>

        <p style="color: #1A1A1A; font-size: 13px; line-height: 1.7; margin: 0 0 24px 0;">
            Sie k&ouml;nnen sich nun direkt &uuml;ber den folgenden Button anmelden:
        </p>

        <!-- CTA Button -->
        <table border="0" cellpadding="0" cellspacing="0" width="100%">
        <tr><td align="center" style="padding: 4px 0 28px 0;">
            <a href="' . esc_url($login_url) . '" style="display: inline-block; background-color: #F68622; color: #FFFFFF; font-family: Helvetica Neue, Helvetica, Arial, sans-serif; font-size: 13px; font-weight: 600; text-decoration: none; padding: 12px 32px; border-radius: 8px;">
                Jetzt anmelden &rarr;
            </a>
        </td></tr>
        </table>

        <!-- Sicherheitshinweis -->
        <table border="0" cellpadding="0" cellspacing="0" width="100%">
        <tr><td style="background-color: #FFF8EC; border-left: 4px solid #F68622; border-radius: 8px; padding: 14px 18px;">
            <p style="margin: 0; font-size: 12px; line-height: 1.6; color: #1A1A1A;">
                <strong>Sicherheitshinweis:</strong><br>
                Bitte &auml;ndern Sie Ihr Passwort nach dem Login in Ihrem Kontobereich.
            </p>
        </td></tr>
        </table>

    </td></tr>

    <!-- Footer -->
    <tr><td style="border-top: 3px solid #F68622; background-color: #1A1A1A; border-radius: 0 0 8px 8px; padding: 24px 48px; text-align: center;">
        <p style="margin: 0 0 6px 0; color: rgba(255,255,255,0.9); font-size: 12px; font-weight: 600;">DP Connect &ndash; Ihr B2B Gro&szlig;handel</p>
        <p style="margin: 0 0 12px 0; color: rgba(255,255,255,0.5); font-size: 11px;">Fragen? Kontaktieren Sie uns jederzeit!</p>
        <p style="margin: 0;">
            <a href="https://dpconnect.de" style="color: #F68622; text-decoration: none; font-weight: 600; font-size: 11px;">Shop</a>
            <span style="color: rgba(255,255,255,0.2); margin: 0 6px;">|</span>
            <a href="mailto:info@dpconnect.de" style="color: #F68622; text-decoration: none; font-weight: 600; font-size: 11px;">E-Mail</a>
            <span style="color: rgba(255,255,255,0.2); margin: 0 6px;">|</span>
            <a href="https://dpconnect.de/mein-konto/" style="color: #F68622; text-decoration: none; font-weight: 600; font-size: 11px;">Mein Konto</a>
        </p>
    </td></tr>

    </table>

    </td></tr>
    </table>

</body>
</html>';

    return wp_mail($to, $subject, $html, ['Content-Type: text/html; charset=UTF-8']);
}

/**
 * REST API Endpoint: Bot-triggered password reset
 */
add_action('rest_api_init', function () {
    register_rest_route('dp/v1', '/bot-reset-password', [
        'methods'  => 'POST',
        'permission_callback' => function ($request) {
            return $request->get_header('X-Bot-Secret') === DP_BOT_SECRET;
        },
        'callback' => function (WP_REST_Request $request) {
            $email = sanitize_email($request->get_param('email'));

            if (!is_email($email)) {
                return new WP_REST_Response([
                    'success' => false,
                    'error'   => 'Ungültige E-Mail-Adresse',
                ], 400);
            }

            $user = get_user_by('email', $email);
            if (!$user) {
                return new WP_REST_Response([
                    'success' => false,
                    'error'   => 'Kein Account mit dieser E-Mail gefunden',
                ], 404);
            }

            // Neues Passwort generieren & setzen
            $new_password = wp_generate_password(12, true, false);
            wp_set_password($new_password, $user->ID);

            // CI-Mail senden (identisches Design wie Admin-Button)
            $mail_sent = dp_bot_send_new_password_mail(
                $user->user_email,
                $user->user_login,
                $new_password,
                $user->last_name
            );

            return new WP_REST_Response([
                'success'   => true,
                'mail_sent' => $mail_sent,
                'message'   => 'Neues Passwort generiert und per E-Mail versendet',
            ], 200);
        },
    ]);
});
