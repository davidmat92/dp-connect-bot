<?php
/**
 * Plugin Name: DP Connect Chat Widget
 * Description: Bestell-Assistent Chat - [dp_chat] und [dp_chat_widget]
 * Version: 3.1
 */
if (!defined('ABSPATH')) exit;

// ============================================================
// CONFIG
// ============================================================
define('DPC_API', 'https://bot-dpconnect.pythonanywhere.com');
define('DPC_LOGO', '/wp-content/uploads/2026/01/logo_dpconnect.webp');
define('DPC_BETA_MODE', true);
// Auto-widget & order toggle are now stored in wp_options (managed via DP Bot admin settings)

// ============================================================
// 🎨 FARBEN
// ============================================================
$DPC_COLORS = [
    'bg'            => '#F2F2F2',
    'bg_header'     => '#ffffff',
    'bg_input'      => '#ffffff',
    'bg_card'       => '#ffffff',
    'bg_input_field'=> '#F7F7F7',
    'accent'        => '#F68622',
    'accent_hover'  => '#E07510',
    'accent_bg'     => 'rgba(246,134,34,.08)',
    'accent_shadow' => 'rgba(246,134,34,.25)',
    'text'          => '#1E1E1E',
    'text_muted'    => '#888888',
    'text_light'    => '#AAAAAA',
    'border'        => '#E5E5E5',
    'border_hover'  => '#F68622',
    'user_bubble'   => '#F68622',
    'user_text'     => '#ffffff',
    'cart_accent'   => '#F68622',
];


// ============================================================
// WC AJAX
// ============================================================
add_action('wp_ajax_dpc_get_cart', 'dpc_ajax_get_cart');
add_action('wp_ajax_nopriv_dpc_get_cart', 'dpc_ajax_get_cart');
function dpc_ajax_get_cart() {
    if (!function_exists('WC') || !WC()->cart) wp_send_json(['ok'=>false]);
    $items = []; $total = 0;
    foreach (WC()->cart->get_cart() as $key => $item) {
        $product = $item['data'];
        $line = $item['line_total'];
        $total += $line;
        $items[] = [
            'key'=>$key, 'product_id'=>$item['product_id'],
            'variation_id'=>$item['variation_id']??0,
            'name'=>$product->get_name(), 'quantity'=>$item['quantity'],
            'price'=>round($line/max($item['quantity'],1),2),
            'line_total'=>round($line,2),
        ];
    }
    wp_send_json(['ok'=>true,'items'=>$items,'count'=>WC()->cart->get_cart_contents_count(),
        'total'=>round($total,2),'cart_url'=>wc_get_cart_url(),'checkout_url'=>wc_get_checkout_url()]);
}

add_action('wp_ajax_dpc_add_to_cart', 'dpc_ajax_add_to_cart');
add_action('wp_ajax_nopriv_dpc_add_to_cart', 'dpc_ajax_add_to_cart');
function dpc_ajax_add_to_cart() {
    if (!function_exists('WC') || !WC()->cart) wp_send_json(['ok'=>false]);
    $pid = absint($_POST['product_id']??0);
    $qty = absint($_POST['quantity']??1);
    if (!$pid||!$qty) wp_send_json(['ok'=>false]);
    $product = wc_get_product($pid);
    if (!$product||!$product->is_purchasable()) wp_send_json(['ok'=>false,'error'=>'N/A']);
    if ($product->is_type('variation'))
        $r = WC()->cart->add_to_cart($product->get_parent_id(),$qty,$pid,$product->get_variation_attributes());
    else $r = WC()->cart->add_to_cart($pid,$qty);
    if ($r) dpc_ajax_get_cart(); else wp_send_json(['ok'=>false]);
}

add_action('wp_ajax_dpc_add_batch', 'dpc_ajax_add_batch');
add_action('wp_ajax_nopriv_dpc_add_batch', 'dpc_ajax_add_batch');
function dpc_ajax_add_batch() {
    if (!function_exists('WC') || !WC()->cart) wp_send_json(['ok'=>false]);
    $items = json_decode(stripslashes($_POST['items']??''),true);
    if (!is_array($items)) wp_send_json(['ok'=>false]);
    foreach ($items as $item) {
        $pid=absint($item['product_id']??0); $qty=absint($item['quantity']??1);
        if (!$pid||!$qty) continue;
        $product=wc_get_product($pid); if (!$product||!$product->is_purchasable()) continue;
        if ($product->is_type('variation'))
            WC()->cart->add_to_cart($product->get_parent_id(),$qty,$pid,$product->get_variation_attributes());
        else WC()->cart->add_to_cart($pid,$qty);
    }
    dpc_ajax_get_cart();
}

add_action('wp_ajax_dpc_remove_from_cart', 'dpc_ajax_remove_from_cart');
add_action('wp_ajax_nopriv_dpc_remove_from_cart', 'dpc_ajax_remove_from_cart');
function dpc_ajax_remove_from_cart() {
    if (!function_exists('WC') || !WC()->cart) wp_send_json(['ok'=>false]);
    $pid = absint($_POST['product_id']??0);
    if (!$pid) wp_send_json(['ok'=>false]);
    foreach (WC()->cart->get_cart() as $key => $item) {
        if ($item['product_id'] == $pid || ($item['variation_id']??0) == $pid) {
            WC()->cart->remove_cart_item($key);
        }
    }
    dpc_ajax_get_cart();
}

add_action('wp_ajax_dpc_clear_cart', 'dpc_ajax_clear_cart');
add_action('wp_ajax_nopriv_dpc_clear_cart', 'dpc_ajax_clear_cart');
function dpc_ajax_clear_cart() {
    if (!function_exists('WC') || !WC()->cart) wp_send_json(['ok'=>false]);
    WC()->cart->empty_cart();
    dpc_ajax_get_cart();
}


// ============================================================
// CSS
// ============================================================
function dpc_css($c, $height = '700px') {
    return '
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap");

.dpc-chat{font-family:"Inter",-apple-system,BlinkMacSystemFont,sans-serif;background:'.$c['bg'].';border-radius:24px;overflow:hidden;display:flex;flex-direction:column;height:'.$height.';max-height:85vh;min-height:400px;position:relative;box-shadow:0 4px 30px rgba(0,0,0,.08)}
.dpc-chat *{margin:0;padding:0;box-sizing:border-box}
.dpc-chat.fullscreen{position:fixed!important;top:0!important;left:0!important;right:0!important;bottom:0!important;width:100%!important;height:100%!important;max-height:100%!important;border-radius:0!important;z-index:100000!important;box-shadow:none!important}

.dpc-hdr{padding:18px 22px;background:'.$c['bg_header'].';border-bottom:1px solid '.$c['border'].';display:flex;align-items:center;gap:14px;flex-shrink:0}
.dpc-av{width:44px;height:44px;border-radius:50%;background:transparent;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:700;color:'.$c['text'].';flex-shrink:0;overflow:hidden}
.dpc-av img{width:100%;height:100%;border-radius:50%;object-fit:cover}
.dpc-hi{flex:1}
.dpc-hn{font-size:16px;font-weight:700;color:'.$c['text'].';letter-spacing:-.02em}
.dpc-hs{font-size:12px;color:'.$c['accent'].';font-weight:500;display:flex;align-items:center;gap:5px;margin-top:1px}
.dpc-hs::before{content:"";width:7px;height:7px;border-radius:50%;background:'.$c['accent'].';display:inline-block}
.dpc-hx,.dpc-hfs{width:34px;height:34px;border-radius:50%;border:none;background:'.$c['bg'].';color:'.$c['text_muted'].';cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:16px;transition:all .2s;font-weight:300;flex-shrink:0}
.dpc-hx:hover,.dpc-hfs:hover{background:'.$c['border'].';color:'.$c['text'].'}
.dpc-hfs svg{width:16px;height:16px;fill:currentColor}

.dpc-wel{padding:20px;flex-shrink:0;animation:dpcFade .5s ease}
@keyframes dpcFade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.dpc-wel-card{background:'.$c['bg_card'].';border-radius:20px;padding:22px;box-shadow:0 1px 6px rgba(0,0,0,.05)}
.dpc-wel-t{font-size:18px;font-weight:700;color:'.$c['text'].';margin-bottom:6px}
.dpc-wel-s{font-size:13px;color:'.$c['text_muted'].';line-height:1.5;margin-bottom:16px}
.dpc-beta{font-size:11px;color:'.$c['text_muted'].';opacity:.7;text-align:center;padding:8px 16px;line-height:1.4;font-style:italic}
.dpc-badge-beta{display:inline-block;font-size:9px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;background:'.$c['accent'].';color:#fff;padding:2px 6px;border-radius:6px;margin-left:6px;vertical-align:middle;line-height:1.2}
.dpc-wel-btns{display:flex;flex-wrap:wrap;gap:7px}
.dpc-wel-btn{padding:8px 15px;border-radius:30px;border:1.5px solid '.$c['border'].';background:'.$c['bg_card'].';color:'.$c['text'].';font-size:12.5px;font-weight:500;font-family:"Inter",sans-serif;cursor:pointer;transition:all .2s;white-space:nowrap}
.dpc-wel-btn:hover{border-color:'.$c['accent'].';color:'.$c['accent'].';transform:translateY(-1px)}
.dpc-reorder-btn{background:'.$c['accent'].' !important;color:#fff !important;border-color:'.$c['accent'].' !important;font-weight:600}
.dpc-reorder-btn:hover{opacity:.9;transform:translateY(-1px)}

/* Support button in welcome */
.dpc-wel-div{height:1px;background:'.$c['border'].';margin:14px 0 10px}

/* Mode selection buttons */
.dpc-mode-btns{display:flex;flex-direction:column;gap:10px}
.dpc-mode-btn{display:flex;align-items:center;gap:14px;padding:16px 18px;border-radius:16px;border:1.5px solid '.$c['border'].';background:'.$c['bg_card'].';cursor:pointer;transition:all .2s;text-align:left;font-family:"Inter",sans-serif;width:100%}
.dpc-mode-btn:hover{border-color:'.$c['accent'].';transform:translateY(-1px);box-shadow:0 4px 12px rgba(246,134,34,.1)}
.dpc-mode-btn:active{transform:translateY(0)}
.dpc-mode-ico{font-size:28px;flex-shrink:0}
.dpc-mode-t{font-size:14px;font-weight:700;color:'.$c['text'].';display:block}
.dpc-mode-s{font-size:11.5px;color:'.$c['text_muted'].';display:block;margin-top:2px}
.dpc-mode-btn.selected{border-color:'.$c['accent'].';background:'.$c['accent_bg'].'}

/* Callback/Contact buttons */
.dpc-cb{display:flex;flex-direction:column;gap:8px;padding:8px 0;animation:dpcIn .3s ease .08s both}
.dpc-cbb{padding:12px 18px;border-radius:14px;border:1.5px solid '.$c['border'].';background:'.$c['bg_card'].';color:'.$c['text'].';font-size:13px;font-weight:500;font-family:"Inter",sans-serif;cursor:pointer;transition:all .2s;text-align:left;display:flex;align-items:center;gap:10px}
.dpc-cbb:hover{border-color:'.$c['accent'].';color:'.$c['accent'].';transform:translateY(-1px)}
.dpc-cbb span{font-size:18px}

/* Guest gate */
.dpc-gate{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 30px;text-align:center;gap:16px}
.dpc-gate-ico{font-size:48px}
.dpc-gate-t{font-size:18px;font-weight:700;color:'.$c['text'].'}
.dpc-gate-s{font-size:13.5px;color:'.$c['text_muted'].';line-height:1.6;max-width:320px}
.dpc-gate-btns{display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:8px}
.dpc-gate-btn{padding:12px 24px;border-radius:30px;font-size:13px;font-weight:700;font-family:"Inter",sans-serif;cursor:pointer;transition:all .2s;text-decoration:none !important;display:inline-flex;align-items:center;gap:6px}
.dpc-gate-btn.primary{background:'.$c['accent'].' !important;color:#fff !important;border:none}
.dpc-gate-btn.primary:hover{background:'.$c['accent_hover'].' !important;transform:translateY(-1px)}
.dpc-gate-btn.secondary{background:transparent;color:'.$c['text'].' !important;border:1.5px solid '.$c['border'].'}
.dpc-gate-btn.secondary:hover{border-color:'.$c['accent'].';color:'.$c['accent'].' !important;transform:translateY(-1px)}

.dpc-msgs{flex:1;overflow-y:auto;padding:10px 20px 20px;display:flex;flex-direction:column;gap:8px;scrollbar-width:thin;scrollbar-color:'.$c['border'].' transparent}
.dpc-msgs::-webkit-scrollbar{width:4px}
.dpc-msgs::-webkit-scrollbar-thumb{background:'.$c['border'].';border-radius:4px}

.dpc-m{max-width:80%;animation:dpcIn .3s cubic-bezier(.34,1.4,.64,1)}
@keyframes dpcIn{from{opacity:0;transform:translateY(6px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}
.dpc-m.b{align-self:flex-start}
.dpc-m.u{align-self:flex-end}
.dpc-mb{padding:12px 16px;font-size:13.5px;line-height:1.6;white-space:pre-wrap;word-break:break-word}
.dpc-m.b .dpc-mb{background:'.$c['bg_card'].';color:'.$c['text'].';border-radius:18px 18px 18px 6px;box-shadow:0 1px 4px rgba(0,0,0,.04)}
.dpc-m.u .dpc-mb{background:'.$c['user_bubble'].';color:'.$c['user_text'].';border-radius:18px 18px 6px 18px}
.dpc-mt{font-size:10px;color:'.$c['text_light'].';margin-top:3px;padding:0 6px}
.dpc-m.u .dpc-mt{text-align:right}

.dpc-tp{display:flex;gap:4px;padding:14px 18px;background:'.$c['bg_card'].';border-radius:18px 18px 18px 6px;width:fit-content;animation:dpcIn .25s ease;box-shadow:0 1px 4px rgba(0,0,0,.04)}
.dpc-tp span{width:6px;height:6px;background:'.$c['accent'].';border-radius:50%;animation:dpcDot 1.3s ease-in-out infinite;opacity:.4}
.dpc-tp span:nth-child(2){animation-delay:.15s}
.dpc-tp span:nth-child(3){animation-delay:.3s}
@keyframes dpcDot{0%,70%,100%{transform:translateY(0);opacity:.3}35%{transform:translateY(-5px);opacity:1}}

.dpc-kb{display:flex;flex-wrap:wrap;gap:6px;padding:6px 0;animation:dpcIn .3s ease .08s both}
.dpc-kb.fl{max-height:260px;overflow-y:auto;padding-right:4px}
.dpc-kbb{padding:8px 15px;border-radius:30px;border:1.5px solid '.$c['border'].';background:'.$c['bg_card'].';color:'.$c['text'].';font-size:12.5px;font-weight:500;font-family:"Inter",sans-serif;cursor:pointer;transition:all .2s;white-space:nowrap}
.dpc-kbb:hover{border-color:'.$c['accent'].';color:'.$c['accent'].';transform:translateY(-1px)}
.dpc-kbb.sel{background:'.$c['accent'].';border-color:'.$c['accent'].';color:#fff}
.dpc-kbb.q{min-width:65px;text-align:center;font-weight:600}
.dpc-kbl{font-size:11px;color:'.$c['text_muted'].';margin-bottom:5px;font-weight:500}
.dpc-kbs{font-size:10px;color:'.$c['text_muted'].';margin-left:3px}

.dpc-ct{padding:12px 22px;background:'.$c['bg_header'].';border-top:1px solid '.$c['border'].';display:none;align-items:center;gap:12px;flex-shrink:0}
.dpc-ct.active{display:flex}
.dpc-ct-ico{font-size:18px}
.dpc-ct-nfo{flex:1}
.dpc-ct-cnt{font-size:13px;color:'.$c['text'].';font-weight:600}
.dpc-ct-sub{font-size:11px;color:'.$c['text_light'].'}
.dpc-ct-tot{font-size:14px;color:'.$c['cart_accent'].';font-weight:700;letter-spacing:-.02em}
.dpc-ct-btn{display:inline-flex;align-items:center;gap:6px;padding:9px 18px;border-radius:30px;background:'.$c['accent'].' !important;border:none;color:#fff !important;font-size:12px;font-weight:700;cursor:pointer;font-family:"Inter",sans-serif;transition:all .2s;text-decoration:none !important}
.dpc-ct-btn:hover{background:'.$c['accent_hover'].' !important;color:#fff !important;transform:translateY(-1px);text-decoration:none !important}
.dpc-ct-btn:visited,.dpc-ct-btn:active,.dpc-ct-btn:focus{color:#fff !important;text-decoration:none !important}

.dpc-ia{padding:16px 20px;background:'.$c['bg_input'].';border-top:1px solid '.$c['border'].';display:flex;gap:10px;align-items:center;flex-shrink:0}
.dpc-in{flex:1;padding:12px 18px;border-radius:30px;border:1.5px solid '.$c['border'].';background:'.$c['bg_input_field'].';color:'.$c['text'].';font-size:13.5px;font-family:"Inter",sans-serif;outline:none;transition:all .2s}
.dpc-in::placeholder{color:'.$c['text_light'].'}
.dpc-in:focus{border-color:'.$c['accent'].';background:#fff;box-shadow:0 0 0 3px '.$c['accent_bg'].'}
.dpc-snd{width:44px;height:44px;border-radius:50%;background:'.$c['accent'].';border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .2s;flex-shrink:0}
.dpc-snd:hover{background:'.$c['accent_hover'].';transform:translateY(-1px)}
.dpc-snd:disabled{opacity:.3;cursor:not-allowed;transform:none}
.dpc-snd svg{width:18px;height:18px;fill:#fff;margin-left:2px}

@media(max-width:640px){.dpc-chat{border-radius:0;height:100vh;max-height:100vh}.dpc-hdr{padding:14px 16px}.dpc-msgs{padding:8px 14px}.dpc-ia{padding:12px 14px}.dpc-wel{padding:14px}.dpc-hfs{display:none}}
';
}


// ============================================================
// JS
// ============================================================
function dpc_js($api, $pfx, $ids, $ajax_url, $wp_user = []) {
    $user_json = !empty($wp_user) ? json_encode($wp_user) : 'null';
    return '
const '.$pfx.'=(()=>{
const API="'.esc_js($api).'";
const AJAX="'.esc_js($ajax_url).'";
const WP_USER='.$user_json.';
const STORE_KEY="dpc_history_"+window.location.hostname;
let chatId=null,ld=false,started=false,pollTimer=null,lastWcCart=null;
const $=id=>document.getElementById(id);

function saveHistory(){
  const box=$("'.$ids['msgs'].'");if(!box)return;
  const entries=[];
  box.querySelectorAll(".dpc-m").forEach(el=>{entries.push({cls:el.className,html:el.innerHTML})});
  try{localStorage.setItem(STORE_KEY,JSON.stringify({chatId,started,entries:entries.slice(-100)}))}catch(e){}
}
function loadHistory(){
  try{
    const raw=localStorage.getItem(STORE_KEY);if(!raw)return false;
    const data=JSON.parse(raw);if(!data.entries||!data.entries.length)return false;
    if(data.chatId)chatId=data.chatId;
    if(data.started){started=true;const w=$("'.$ids['welcome'].'");if(w)w.remove()}
    const box=$("'.$ids['msgs'].'");
    data.entries.forEach(e=>{const el=document.createElement("div");el.className=e.cls;el.innerHTML=e.html;el.style.animation="none";box.appendChild(el)});
    requestAnimationFrame(()=>box.scrollTop=box.scrollHeight);
    return true;
  }catch(e){return false}
}

function pollWcCart(){
  fetch(AJAX+"?action=dpc_get_cart",{credentials:"same-origin"})
    .then(r=>r.json()).then(d=>{if(d.ok)renderWcCart(d)}).catch(()=>{});
}
function renderWcCart(d){
  lastWcCart=d;const bar=$("'.$ids['cart'].'");
  if(d.count>0){bar.classList.add("active");
    $("'.$ids['cart_count'].'").textContent=d.count+" Produkt"+(d.count>1?"e":"");
    $("'.$ids['cart_total'].'").textContent=d.total.toFixed(2).replace(".",",")+"\u20AC";
    $("'.$ids['cart_btn'].'").href=d.cart_url;
  }else bar.classList.remove("active");
}
function addToWcCart(pid,qty){
  const fd=new FormData();fd.append("action","dpc_add_to_cart");fd.append("product_id",pid);fd.append("quantity",qty);
  return fetch(AJAX,{method:"POST",body:fd,credentials:"same-origin"}).then(r=>r.json()).then(d=>{if(d.ok)renderWcCart(d);return d}).catch(()=>({ok:false}));
}
function removeFromWcCart(pid){
  const fd=new FormData();fd.append("action","dpc_remove_from_cart");fd.append("product_id",pid);
  return fetch(AJAX,{method:"POST",body:fd,credentials:"same-origin"}).then(r=>r.json()).then(d=>{if(d.ok)renderWcCart(d);return d}).catch(()=>({ok:false}));
}
function clearWcCart(){
  const fd=new FormData();fd.append("action","dpc_clear_cart");
  return fetch(AJAX,{method:"POST",body:fd,credentials:"same-origin"}).then(r=>r.json()).then(d=>{if(d.ok)renderWcCart(d);return d}).catch(()=>({ok:false}));
}
async function processWcActions(actions){
  if(!actions||!actions.length){pollWcCart();return}
  for(const a of actions){
    if(a.action==="add")await addToWcCart(a.product_id,a.quantity);
    else if(a.action==="remove")await removeFromWcCart(a.product_id);
    else if(a.action==="clear")await clearWcCart();
  }
}

(async function(){
  const hadHistory=loadHistory();
  const vid=localStorage.getItem("dp_visitor")||(()=>{const id=Date.now().toString(36)+Math.random().toString(36);localStorage.setItem("dp_visitor",id);return id})();
  if(!chatId){try{const initData={visitor_id:vid};if(WP_USER){initData.wp_user_id=WP_USER.id;initData.wp_display_name=WP_USER.name;initData.wp_email=WP_USER.email;initData.wp_username=WP_USER.login;initData.customer_name=WP_USER.name}const r=await fetch(API+"/chat/init",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(initData)});const d=await r.json();if(d.ok)chatId=d.chat_id;saveHistory()}catch(e){}}
  pollWcCart();pollTimer=setInterval(pollWcCart,15000);
})();

function hw(){if(!started){started=true;const w=$("'.$ids['welcome'].'");if(w){w.style.transition="all .3s";w.style.opacity="0";w.style.maxHeight="0";w.style.padding="0";w.style.overflow="hidden";setTimeout(()=>w.remove(),300)}saveHistory()}}

async function send(t){
  const msg=t||$("'.$ids['input'].'").value.trim();if(!msg||!chatId||ld)return;if(!t)$("'.$ids['input'].'").value="";
  hw();addMsg(msg,"u");showT();ld=true;$("'.$ids['send'].'").disabled=true;
  try{const payload={chat_id:chatId,message:msg};if(lastWcCart&&lastWcCart.items)payload.wc_cart=lastWcCart.items;
  const r=await fetch(API+"/chat/send",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const d=await r.json();hideT();
  if(d.ok){addMsg(d.text,"b");if(d.keyboards&&d.keyboards.length)rKB(d.keyboards);
    await processWcActions(d.wc_actions);
  }else addMsg("Fehler – nochmal versuchen! 🔄","b")}
  catch(e){hideT();addMsg("Verbindungsfehler.","b")}ld=false;$("'.$ids['send'].'").disabled=false;$("'.$ids['input'].'").focus();
}

async function sAct(cb){
  if(!chatId)return;showT();
  try{const r=await fetch(API+"/chat/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chat_id:chatId,callback:cb})});const d=await r.json();hideT();
  if(d.ok){if(d.text)addMsg(d.text,"b");if(d.keyboards&&d.keyboards.length)rKB(d.keyboards);
    await processWcActions(d.wc_actions);
    if(d.awaiting_input)$("'.$ids['input'].'").focus();
  }}catch(e){hideT()}
}

function addMsg(text,type){
  const box=$("'.$ids['msgs'].'"),now=new Date(),time=String(now.getHours()).padStart(2,"0")+":"+String(now.getMinutes()).padStart(2,"0");
  let html=text.replace(/\\*\\*(.*?)\\*\\*/g,"<strong>$1</strong>").replace(/\\*(.*?)\\*/g,"<strong>$1</strong>");
  const el=document.createElement("div");el.className="dpc-m "+type;
  el.innerHTML=\'<div class="dpc-mb">\'+html+\'</div><div class="dpc-mt">\'+time+\'</div>\';
  box.appendChild(el);requestAnimationFrame(()=>box.scrollTop=box.scrollHeight);saveHistory();
}
function showT(){const box=$("'.$ids['msgs'].'"),el=document.createElement("div");el.className="dpc-m b";el.id="'.$ids['typing'].'";el.innerHTML=\'<div class="dpc-tp"><span></span><span></span><span></span></div>\';box.appendChild(el);box.scrollTop=box.scrollHeight}
function hideT(){const el=$("'.$ids['typing'].'");if(el)el.remove()}

function rKB(kbs){
  const box=$("'.$ids['msgs'].'");
  kbs.forEach(kb=>{const w=document.createElement("div");w.className="dpc-m b";
  if(kb.type==="flavors"){let h=\'<div class="dpc-kbl">Geschmack wählen:</div><div class="dpc-kb fl">\';kb.buttons.forEach(b=>{h+=\'<button class="dpc-kbb" onclick="'.$pfx.'.selF(this,\\\'\'+b.callback+\'\\\')">\'+b.label+\'<span class="dpc-kbs">\'+b.sublabel+\'</span></button>\'});h+=\'</div>\';w.innerHTML=h}
  else if(kb.type==="quantities"){let h=\'<div class="dpc-kbl">\'+kb.label+\' (\'+kb.price+\'/Stk)</div><div class="dpc-kb">\';kb.buttons.forEach(b=>{h+=\'<button class="dpc-kbb q" onclick="'.$pfx.'.sAct(\\\'\'+b.callback+\'\\\')">\'+b.qty+\' Stk</button>\'});h+=\'<button class="dpc-kbb q" style="border-style:dashed;opacity:.6" onclick="'.$pfx.'.cQty(\\\'\'+kb.product_id+\'\\\')">\u270F\uFE0F</button></div>\';w.innerHTML=h}
  else if(kb.type==="callback"){let h=\'<div class="dpc-cb">\';kb.buttons.forEach(b=>{h+=\'<button class="dpc-cbb" onclick="'.$pfx.'.sAct(\\\'\'+b.callback+\'\\\');">\'+b.label+\'</button>\'});h+=\'</div>\';w.innerHTML=h}
  box.appendChild(w);requestAnimationFrame(()=>box.scrollTop=box.scrollHeight)});saveHistory();
}

function selF(btn,cb){btn.classList.add("sel");btn.closest(".dpc-kb").querySelectorAll(".dpc-kbb").forEach(b=>{if(b!==btn){b.style.opacity=".35";b.style.pointerEvents="none"}});sAct(cb)}
function cQty(pid){const q=prompt("Wie viele Stück?");if(q&&!isNaN(parseInt(q))){sAct("custom_"+pid);setTimeout(()=>send(q.toString()),600)}}

function toggleFs(){
  const btn=$("'.$ids['fs'].'");if(!btn)return;
  const chat=btn.closest(".dpc-chat");if(!chat)return;
  // Widget: fullscreen auf #dpc-window, Embed: auf .dpc-chat
  const win=chat.closest("#dpc-window");
  if(win){win.classList.toggle("dpc-fs");chat.classList.toggle("fullscreen")}
  else{chat.classList.toggle("fullscreen")}
  const isFs=chat.classList.contains("fullscreen");
  btn.innerHTML=isFs
    ?\'<svg viewBox="0 0 24 24"><path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>\'
    :\'<svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>\';
}

function checkout(){const btn=$("'.$ids['cart_btn'].'");if(btn&&btn.href)window.location.href=btn.href}
function clearHistory(){try{localStorage.removeItem(STORE_KEY)}catch(e){}}
function openSupport(productName){
  hw();
  if(productName){send("[PRODUKT-ANFRAGE: "+productName+"] Ich habe eine Frage zu diesem Produkt.")}
  else{send("[SUPPORT] Ich brauche Hilfe vom Kundenservice")}
}

function setMode(mode, btn){
  // Highlight selected button
  if(btn){btn.classList.add("selected");btn.closest(".dpc-mode-btns").querySelectorAll(".dpc-mode-btn").forEach(b=>{if(b!==btn){b.style.opacity="0.4";b.style.pointerEvents="none"}})}

  if(mode==="order"){
    // Show product hints
    const hints=document.getElementById("'.$ids['welcome'].'")?.querySelector(".dpc-order-hints");
    if(hints)hints.style.display="block";
    // Check if last order exists on server for reorder button
    checkReorder();
  } else if(mode==="support"){
    hw();
    addMsg("🎧 Kundenservice","b");
    addMsg("Klar, ich leite dich weiter! Beschreib mir kurz dein Anliegen, damit Davides Team direkt Bescheid weiß. ✍️","b");
    saveHistory();
    // Set support mode on server via first message
    fetch(API+"/chat/action",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chat_id:chatId,callback:"set_support_mode"})});
  }
}

async function checkReorder(){
  if(!chatId)return;
  try{
    const r=await fetch(API+"/chat/has_last_order?chat_id="+encodeURIComponent(chatId));
    const d=await r.json();
    if(d.ok&&d.has_last_order&&d.items&&d.items.length>0){
      const hints=document.getElementById("'.$ids['welcome'].'")?.querySelector(".dpc-order-hints");
      if(hints){
        const rb=document.createElement("button");
        rb.className="dpc-wel-btn dpc-reorder-btn";
        rb.innerHTML="🔄 Letzte Bestellung nochmal";
        rb.onclick=function(){hw();sAct("reorder")};
        const btns=hints.querySelector(".dpc-wel-btns");
        if(btns)btns.prepend(rb);
      }
    }
  }catch(e){}
}

return{send,quickSend:t=>{$("'.$ids['input'].'").value=t;send()},sAct,selF,cQty,checkout,clearHistory,pollWcCart,toggleFs,openSupport,setMode};
})();
';
}


// ============================================================
// GUEST GATE HTML
// ============================================================
function dpc_guest_gate_html() {
    return '
    <div class="dpc-gate">
        <div class="dpc-gate-ico">🔒</div>
        <div class="dpc-gate-t">Bestell-Bot nur für Kunden</div>
        <div class="dpc-gate-s">Melde dich an, um den Bestell-Assistenten zu nutzen und alle B2B-Preise auf dpconnect.de zu sehen.</div>
        <div class="dpc-gate-btns">
            <a class="dpc-gate-btn primary" href="/anmelden/">Anmelden</a>
            <a class="dpc-gate-btn secondary" href="/kunde-werden/">Kunde werden</a>
        </div>
    </div>';
}


// ============================================================
// FULLSCREEN BUTTON SVG
// ============================================================
function dpc_fs_btn_html($id) {
    return '<button class="dpc-hfs" id="'.$id.'" onclick="'
        . ($id === 'dpe-fs' ? 'DPE' : 'DPW')
        . '.toggleFs()" title="Vollbild"><svg viewBox="0 0 24 24"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg></button>';
}


// ============================================================
// [dp_chat] - Embedded
// ============================================================
function dpc_embed_shortcode($atts) {
    global $DPC_COLORS;
    $atts = shortcode_atts(['height' => '700px'], $atts);
    $api = DPC_API;
    $logo = DPC_LOGO;
    $ajax_url = admin_url('admin-ajax.php');
    $is_logged_in = is_user_logged_in();
    $ids = ['wrap'=>'dpe-wrap','msgs'=>'dpe-m','input'=>'dpe-i','send'=>'dpe-s','cart'=>'dpe-c',
            'cart_count'=>'dpe-cc','cart_total'=>'dpe-ct','cart_btn'=>'dpe-cb','welcome'=>'dpe-w',
            'typing'=>'dpe-t','fs'=>'dpe-fs'];

    ob_start();
    echo '<style>' . dpc_css($DPC_COLORS, $atts['height']) . '</style>';
    ?>
<div id="dpe-wrap">
<div class="dpc-chat">
  <div class="dpc-hdr">
    <div class="dpc-av"><img src="<?php echo esc_url($logo); ?>" alt="DP" onerror="this.remove();this.parentNode.textContent='DP'"></div>
    <div class="dpc-hi"><div class="dpc-hn">DP Connect<?php if (DPC_BETA_MODE): ?><span class="dpc-badge-beta">Beta</span><?php endif; ?></div><div class="dpc-hs">Online</div></div>
    <?php echo dpc_fs_btn_html('dpe-fs'); ?>
  </div>
<?php if ($is_logged_in): ?>
  <div class="dpc-wel" id="dpe-w">
    <div class="dpc-wel-card">
      <div class="dpc-wel-t">Hey! 👋</div>
      <div class="dpc-wel-s">Willkommen bei DP Connect. Wie können wir dir helfen?</div>
      <?php if (DPC_BETA_MODE): ?><div class="dpc-beta">* Dieser Bot befindet sich in einer frühen Testphase. Fehler bitte entschuldigen! Auf dpconnect.de kannst du ganz normal stöbern und bestellen.</div><?php endif; ?>
      <div class="dpc-mode-btns">
        <?php if (get_option('dpc_order_enabled', true)): ?>
        <button class="dpc-mode-btn" onclick="DPE.setMode('order',this)">
          <span class="dpc-mode-ico">🛒</span>
          <span class="dpc-mode-t">Bestellen</span>
          <span class="dpc-mode-s">Produkte suchen &amp; in den Warenkorb</span>
        </button>
        <?php endif; ?>
        <button class="dpc-mode-btn" onclick="DPE.setMode('support',this)">
          <span class="dpc-mode-ico">🎧</span>
          <span class="dpc-mode-t">Kundenservice</span>
          <span class="dpc-mode-s">Fragen, Retouren, Lieferstatus &amp; mehr</span>
        </button>
      </div>
    </div>
    <?php if (get_option('dpc_order_enabled', true)): ?>
    <!-- Bestell-Vorschläge (initial hidden, shown after mode=order) -->
    <div class="dpc-wel-card dpc-order-hints" id="dpe-hints" style="display:none;margin-top:10px">
      <div class="dpc-wel-s">Was suchst du?</div>
      <div class="dpc-wel-btns">
        <button class="dpc-wel-btn" onclick="DPE.quickSend('Was läuft gerade am besten?')">🔥 Bestseller</button>
        <button class="dpc-wel-btn" onclick="DPE.quickSend('Was habt ihr an Elf Bar Pods?')">🔋 Elf Bar Pods</button>
        <button class="dpc-wel-btn" onclick="DPE.quickSend('Zeig mir eure Einweg Vapes')">💨 Einweg Vapes</button>
        <button class="dpc-wel-btn" onclick="DPE.quickSend('Was habt ihr an Liquids?')">💧 Liquids</button>
        <button class="dpc-wel-btn" onclick="DPE.quickSend('Zeig mir Shisha Tabak')">🌿 Shisha Tabak</button>
        <button class="dpc-wel-btn" onclick="DPE.quickSend('Was habt ihr an Snacks und Drinks?')">🍬 Snacks &amp; Drinks</button>
      </div>
    </div>
    <?php endif; ?>
  </div>
  <div class="dpc-msgs" id="dpe-m"></div>
  <div class="dpc-ct" id="dpe-c">
    <span class="dpc-ct-ico">🛒</span>
    <div class="dpc-ct-nfo"><div class="dpc-ct-cnt" id="dpe-cc">0 Produkte</div><div class="dpc-ct-sub">netto</div></div>
    <div class="dpc-ct-tot" id="dpe-ct">0,00€</div>
    <a class="dpc-ct-btn" id="dpe-cb" href="<?php echo esc_url(wc_get_cart_url()); ?>">Warenkorb öffnen →</a>
  </div>
  <div class="dpc-ia">
    <input class="dpc-in" id="dpe-i" placeholder="Schreib was du brauchst ..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();DPE.send()}" autocomplete="off">
    <button class="dpc-snd" id="dpe-s" onclick="DPE.send()"><svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" fill="white"/></svg></button>
  </div>
  <script><?php
    $wp_user_data = [];
    if ($is_logged_in) {
        $u = wp_get_current_user();
        $wp_user_data = ['id'=>$u->ID,'name'=>$u->display_name,'email'=>$u->user_email,'login'=>$u->user_login];
    }
    echo dpc_js($api, 'DPE', $ids, $ajax_url, $wp_user_data);
  ?></script>
<?php else: ?>
  <?php echo dpc_guest_gate_html(); ?>
<?php endif; ?>
</div>
</div>
    <?php
    return ob_get_clean();
}
add_shortcode('dp_chat', 'dpc_embed_shortcode');


// ============================================================
// [dp_chat_widget] - Floating Widget
// ============================================================
function dpc_widget_shortcode($atts) {
    global $DPC_COLORS;
    $c = $DPC_COLORS;
    $api = DPC_API;
    $logo = DPC_LOGO;
    $ajax_url = admin_url('admin-ajax.php');
    $is_logged_in = is_user_logged_in();
    $ids = ['wrap'=>'dpw-wrap','msgs'=>'dpw-m','input'=>'dpw-i','send'=>'dpw-s','cart'=>'dpw-c',
            'cart_count'=>'dpw-cc','cart_total'=>'dpw-ct','cart_btn'=>'dpw-cb','welcome'=>'dpw-w',
            'typing'=>'dpw-t','fs'=>'dpw-fs'];

    ob_start();
    echo '<style>' . dpc_css($c, '560px');
    ?>

#dpc-launcher{position:fixed;bottom:24px;right:24px;width:60px;height:60px;border-radius:50%;background:<?php echo $c['accent'];?>;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 16px <?php echo $c['accent_shadow'];?>;transition:all .3s cubic-bezier(.34,1.56,.64,1);z-index:99998}
#dpc-launcher:hover{transform:scale(1.08);background:<?php echo $c['accent_hover'];?>}
#dpc-launcher.open{transform:rotate(45deg) scale(.9)}
#dpc-launcher svg{width:26px;height:26px;fill:#fff}
#dpc-window{position:fixed!important;bottom:96px;right:24px;width:400px;max-width:calc(100vw - 32px);z-index:99999;opacity:0;transform:translateY(16px) scale(.96);pointer-events:none;transition:all .3s cubic-bezier(.34,1.56,.64,1);display:flex;flex-direction:column;justify-content:flex-end}
#dpc-window.visible{opacity:1;transform:translateY(0) scale(1);pointer-events:auto}
#dpc-window .dpc-chat{height:660px;max-height:calc(100vh - 130px);box-shadow:0 8px 40px rgba(0,0,0,.12)}
#dpc-window .dpc-chat.fullscreen{max-height:100%}
#dpc-window.dpc-fs{top:0!important;left:0!important;right:0!important;bottom:0!important;width:100%!important;max-width:100%!important;border-radius:0}
#dpc-window.dpc-fs .dpc-chat{height:100%!important;max-height:100%!important;border-radius:0!important}
@media(max-width:480px){
  #dpc-window{position:fixed!important;top:0!important;left:0!important;right:0!important;bottom:0!important;width:100%!important;max-width:100%!important;transform:none!important}
  #dpc-window.visible{transform:none!important}
  #dpc-window .dpc-chat{height:100%!important;max-height:100%!important;border-radius:0!important}
  #dpc-launcher{bottom:16px;right:16px}
  #dpc-window .dpc-hfs{display:none}
}
</style>

<button id="dpc-launcher" onclick="DPW.toggle()">
  <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/><path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg>
</button>
<div id="dpc-window">
<div class="dpc-chat">
  <div class="dpc-hdr">
    <div class="dpc-av"><img src="<?php echo esc_url($logo); ?>" alt="DP" onerror="this.remove();this.parentNode.textContent='DP'"></div>
    <div class="dpc-hi"><div class="dpc-hn">DP Connect<?php if (DPC_BETA_MODE): ?><span class="dpc-badge-beta">Beta</span><?php endif; ?></div><div class="dpc-hs">Online</div></div>
    <?php echo dpc_fs_btn_html('dpw-fs'); ?>
    <button class="dpc-hx" onclick="DPW.toggle()">✕</button>
  </div>
<?php if ($is_logged_in): ?>
  <div class="dpc-wel" id="dpw-w">
    <div class="dpc-wel-card">
      <div class="dpc-wel-t">Hey! 👋</div>
      <div class="dpc-wel-s">Wie können wir dir helfen?</div>
      <?php if (DPC_BETA_MODE): ?><div class="dpc-beta">* Dieser Bot befindet sich in einer frühen Testphase. Fehler bitte entschuldigen! Auf dpconnect.de kannst du ganz normal stöbern und bestellen.</div><?php endif; ?>
      <div class="dpc-mode-btns">
        <?php if (get_option('dpc_order_enabled', true)): ?>
        <button class="dpc-mode-btn" onclick="DPW.setMode('order',this)">
          <span class="dpc-mode-ico">🛒</span>
          <div><span class="dpc-mode-t">Bestellen</span><span class="dpc-mode-s">Produkte suchen &amp; in den Warenkorb</span></div>
        </button>
        <?php endif; ?>
        <button class="dpc-mode-btn" onclick="DPW.setMode('support',this)">
          <span class="dpc-mode-ico">🎧</span>
          <div><span class="dpc-mode-t">Kundenservice</span><span class="dpc-mode-s">Fragen, Retouren, Lieferstatus &amp; mehr</span></div>
        </button>
      </div>
    </div>
    <?php if (get_option('dpc_order_enabled', true)): ?>
    <div class="dpc-wel-card dpc-order-hints" id="dpw-hints" style="display:none;margin-top:10px">
      <div class="dpc-wel-s">Was suchst du?</div>
      <div class="dpc-wel-btns">
        <button class="dpc-wel-btn" onclick="DPW.quickSend('Was läuft gerade am besten?')">🔥 Bestseller</button>
        <button class="dpc-wel-btn" onclick="DPW.quickSend('Was habt ihr an Elf Bar Pods?')">🔋 Elf Bar Pods</button>
        <button class="dpc-wel-btn" onclick="DPW.quickSend('Zeig mir eure Einweg Vapes')">💨 Einweg Vapes</button>
        <button class="dpc-wel-btn" onclick="DPW.quickSend('Was habt ihr an Liquids?')">💧 Liquids</button>
        <button class="dpc-wel-btn" onclick="DPW.quickSend('Zeig mir Shisha Tabak')">🌿 Shisha Tabak</button>
        <button class="dpc-wel-btn" onclick="DPW.quickSend('Was habt ihr an Snacks und Drinks?')">🍬 Snacks &amp; Drinks</button>
      </div>
    </div>
    <?php endif; ?>
  </div>
  <div class="dpc-msgs" id="dpw-m"></div>
  <div class="dpc-ct" id="dpw-c">
    <span class="dpc-ct-ico">🛒</span>
    <div class="dpc-ct-nfo"><div class="dpc-ct-cnt" id="dpw-cc">0 Produkte</div><div class="dpc-ct-sub">netto</div></div>
    <div class="dpc-ct-tot" id="dpw-ct">0,00€</div>
    <a class="dpc-ct-btn" id="dpw-cb" href="<?php echo esc_url(wc_get_cart_url()); ?>">Warenkorb öffnen →</a>
  </div>
  <div class="dpc-ia">
    <input class="dpc-in" id="dpw-i" placeholder="Schreib was du brauchst ..." onkeydown="if(event.key==='Enter')DPW.send()" autocomplete="off">
    <button class="dpc-snd" id="dpw-s" onclick="DPW.send()"><svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" fill="white"/></svg></button>
  </div>
  <script>
  <?php
    $wp_user_data = [];
    if ($is_logged_in) {
        $u = wp_get_current_user();
        $wp_user_data = ['id'=>$u->ID,'name'=>$u->display_name,'email'=>$u->user_email,'login'=>$u->user_login];
    }
    echo dpc_js($api, 'DPW', $ids, $ajax_url, $wp_user_data);
  ?>
  DPW.toggle=function(){
    const w=document.getElementById("dpc-window"),l=document.getElementById("dpc-launcher");
    const o=w.classList.toggle("visible");l.classList.toggle("open",o);
    if(o)setTimeout(()=>document.getElementById("dpw-i").focus(),350);
    // Exit fullscreen when closing
    if(!o){w.classList.remove("dpc-fs");const c=w.querySelector(".dpc-chat");if(c)c.classList.remove("fullscreen")}
  };
  </script>
<?php else: ?>
  <?php echo dpc_guest_gate_html(); ?>
  <script>
  var DPW={toggle:function(){
    const w=document.getElementById("dpc-window"),l=document.getElementById("dpc-launcher");
    w.classList.toggle("visible");l.classList.toggle("open");
  }};
  </script>
<?php endif; ?>
</div>
</div>
    <?php
    return ob_get_clean();
}
add_shortcode('dp_chat_widget', 'dpc_widget_shortcode');


// ============================================================
// [dp_chat_support] - Live Chat Button für Produktseiten
// ============================================================
function dpc_support_shortcode($atts) {
    global $DPC_COLORS;
    $c = $DPC_COLORS;
    $atts = shortcode_atts(['product' => ''], $atts);

    // Produkt-Name automatisch holen wenn nicht angegeben
    $product_name = $atts['product'];
    if (!$product_name && function_exists('wc_get_product')) {
        global $product;
        if ($product && is_object($product)) {
            $product_name = $product->get_name();
        } elseif (is_product()) {
            $p = wc_get_product(get_the_ID());
            if ($p) $product_name = $p->get_name();
        }
    }
    if (!$product_name) $product_name = get_the_title();

    $product_js = esc_js($product_name);

    ob_start();
    ?>
    <a href="javascript:void(0)" class="dpc-support-link" onclick="dpcOpenProductChat('<?php echo $product_js; ?>')" style="display:inline-flex;align-items:center;gap:8px;font-family:'Inter',-apple-system,sans-serif;font-size:16px;font-weight:700;color:<?php echo $c['text']; ?>;text-decoration:none;cursor:pointer;transition:color .2s" onmouseover="this.style.color='<?php echo $c['accent']; ?>'" onmouseout="this.style.color='<?php echo $c['text']; ?>'">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/><path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg>
        LIVE CHAT
    </a>
    <script>
    function dpcOpenProductChat(productName) {
        // Widget öffnen (falls vorhanden)
        if (typeof DPW !== 'undefined' && DPW.toggle) {
            const w = document.getElementById('dpc-window');
            if (w && !w.classList.contains('visible')) DPW.toggle();
            // Warte kurz auf Init, dann Support senden
            setTimeout(function() {
                if (typeof DPW.openSupport === 'function') DPW.openSupport(productName);
            }, 500);
        }
        // Fallback: Embed-Chat
        else if (typeof DPE !== 'undefined' && DPE.openSupport) {
            DPE.openSupport(productName);
        }
        // Kein Chat vorhanden → WhatsApp Fallback
        else {
            window.open('https://wa.me/4915906192252?text=' + encodeURIComponent('Frage zu: ' + productName), '_blank');
        }
    }
    </script>
    <?php
    return ob_get_clean();
}
add_shortcode('dp_chat_support', 'dpc_support_shortcode');

if (get_option('dpc_auto_widget', false)) {
    add_action('wp_footer', function() { if (!is_admin()) echo do_shortcode('[dp_chat_widget]'); });
}

// Global function – always available when chat widget is loaded
add_action('wp_footer', function() {
    if (is_admin()) return;
    ?>
    <script>
    function dpcOpenProductChat(productName) {
        if (!productName) {
            var el = document.querySelector('.product_title, h1.entry-title');
            if (el) productName = el.textContent.trim();
        }
        if (typeof DPW !== 'undefined' && DPW.openSupport) {
            var w = document.getElementById('dpc-window');
            if (w && !w.classList.contains('visible') && DPW.toggle) DPW.toggle();
            setTimeout(function() { DPW.openSupport(productName); }, 500);
        } else if (typeof DPE !== 'undefined' && DPE.openSupport) {
            DPE.openSupport(productName);
        } else {
            var msg = productName ? 'Frage zu: ' + productName : 'Hallo, ich brauche Hilfe';
            window.open('https://wa.me/4915906192252?text=' + encodeURIComponent(msg), '_blank');
        }
    }
    </script>
    <?php
}, 99);