"""
Voice message transcription via OpenAI Whisper API.
Supports Telegram and WhatsApp voice messages.
"""

import os
import tempfile
import requests

from dp_connect_bot.config import (
    OPENAI_API_KEY, TELEGRAM_API, TELEGRAM_TOKEN,
    WHATSAPP_TOKEN, WHATSAPP_API, log,
)

# Whisper-Limit ist 25 MB — groessere Audios gar nicht erst hochladen (sparen den
# fehlschlagenden API-Call). WhatsApp-/Telegram-Sprachnachrichten sind praktisch
# immer << das; dies ist ein defensiver Guard.
_MAX_AUDIO_BYTES = 24 * 1024 * 1024


def _whisper_vocab_prompt():
    """Marken-Glossar als Whisper-Bias-Prompt — sonst werden Produktnamen
    phonetisch verschrieben ('Elflick' statt ELFLIQ, 'Elfapots' statt ELFA Pods)."""
    fixed = ["ELFLIQ", "ELFA Pods", "Elf Bar", "ELFX", "Lost Mary", "Tappo"]
    try:
        from collections import Counter
        from dp_connect_bot.services.product_cache import cache
        counts = Counter(p.get("brand", "") for p in cache.available if p.get("brand"))
        top = [b for b, _ in counts.most_common(35) if b and b.lower() not in
               {f.lower() for f in fixed}]
        vocab = fixed + top
    except Exception:
        vocab = fixed
    return "Bestellung im Vape-Großhandel. Produktnamen: " + ", ".join(vocab) + "."


# Bekannte Whisper-Halluzinationen bei Stille/Rauschen (Untertitel-/YouTube-
# Artefakte). Whisper-1 "erfindet" die gern bei leeren Audios → sonst wuerde ein
# versehentliches/leeres Voice als Phantom-"Bestellung" verarbeitet.
_WHISPER_NOISE = {
    "vielen dank", "vielen dank fürs zuschauen", "vielen dank fürs zugucken",
    "danke fürs zuschauen", "danke fürs zugucken", "bis zum nächsten mal",
    "bis zum nächsten video", "das war's für heute", "wir sehen uns",
    "auf wiedersehen", "tschüss", "ciao", "amara.org",
    "untertitel der amara.org-community", "untertitelung aufgrund der amara.org-community",
    "untertitel im auftrag des zdf", "untertitelung des zdf, 2020",
}


def _looks_like_no_speech(text):
    """True, wenn die Transkription keine echte Sprache enthaelt (leer, nur
    Symbole, oder eine bekannte Whisper-Halluzination)."""
    if not text:
        return True
    t = text.strip().lower().strip("!.?…\"' ").strip()
    if not t:
        return True
    if all(not ch.isalnum() for ch in t):  # nur Symbole/Musiknoten
        return True
    if t in _WHISPER_NOISE:
        return True
    if "untertitel" in t and len(t) < 60:
        return True
    if "amara.org" in t:
        return True
    return False


def _alert_if_outage(resp):
    """Bei Whisper-Auth-/Guthaben-Fehler (401/429/quota) einmalig Davide alarmieren
    — sonst faellt Voice still aus (jede Sprachnachricht 'nicht verstanden')."""
    try:
        body = (resp.text or "").lower()
        if resp.status_code in (401, 402, 429) or "insufficient_quota" in body or "exceeded your current quota" in body:
            from dp_connect_bot.services.pushover import notify_voice_outage
            notify_voice_outage(f"OpenAI/Whisper antwortet mit HTTP {resp.status_code} — Sprachnachrichten werden gerade nicht transkribiert (Key/Guthaben pruefen).")
    except Exception:
        pass


def transcribe_telegram_voice(file_id):
    """Transkribiert eine Telegram Voice Message via OpenAI Whisper API."""
    if not OPENAI_API_KEY:
        log.warning("OpenAI API Key fehlt - Voice Message kann nicht transkribiert werden")
        return None

    tmp_path = None
    try:
        resp = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=10)
        resp.raise_for_status()
        file_path = resp.json()["result"]["file_path"]

        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        audio_resp = requests.get(file_url, timeout=30)
        audio_resp.raise_for_status()
        if len(audio_resp.content) > _MAX_AUDIO_BYTES:
            log.warning(f"Voice (Telegram) zu gross ({len(audio_resp.content)} bytes) - uebersprungen")
            return None

        suffix = ".ogg" if "ogg" in file_path else ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_resp.content)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as audio_file:
            whisper_resp = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": (f"voice{suffix}", audio_file, f"audio/{suffix.strip('.')}")},
                # KEIN festes language=de → Whisper erkennt die Sprache automatisch
                # (Tuerkisch/Arabisch/Englisch …); der Marken-Vocab-Prompt haelt die
                # Produktnamen sprachunabhaengig stabil.
                data={"model": "whisper-1", "prompt": _whisper_vocab_prompt()},
                timeout=30,
            )
            if not whisper_resp.ok:
                _alert_if_outage(whisper_resp)
                whisper_resp.raise_for_status()
            text = whisper_resp.json().get("text", "").strip()

        os.unlink(tmp_path)
        if _looks_like_no_speech(text):
            log.info(f"Voice (Telegram) als No-Speech/Halluzination verworfen: '{text}'")
            return None
        log.info(f"Voice transcribed: '{text}'")
        return text

    except Exception as e:
        log.error(f"Voice transcription error: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return None


def transcribe_whatsapp_voice(media_id):
    """Transkribiert eine WhatsApp Voice Message via OpenAI Whisper API."""
    if not OPENAI_API_KEY:
        log.error("OPENAI_API_KEY fehlt - Voice kann nicht transkribiert werden!")
        return None
    if not WHATSAPP_TOKEN:
        log.error("WHATSAPP_TOKEN fehlt - Voice kann nicht transkribiert werden!")
        return None

    tmp_path = None
    try:
        # Step 1: Get media URL from WhatsApp
        log.info(f"Voice: Lade Media-URL fuer {media_id}")
        resp = requests.get(
            f"{WHATSAPP_API}/{media_id}",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        media_url = resp.json().get("url")
        if not media_url:
            log.error(f"Voice: Keine media_url in Response: {resp.text[:200]}")
            return None

        # Step 2: Download audio file
        log.info(f"Voice: Lade Audio-Datei herunter")
        audio_resp = requests.get(
            media_url,
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            timeout=30,
        )
        audio_resp.raise_for_status()
        log.info(f"Voice: Audio heruntergeladen, {len(audio_resp.content)} bytes")
        if len(audio_resp.content) > _MAX_AUDIO_BYTES:
            log.warning(f"Voice (WhatsApp) zu gross ({len(audio_resp.content)} bytes) - uebersprungen")
            return None

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio_resp.content)
            tmp_path = tmp.name

        # Step 3: Transcribe via Whisper
        log.info(f"Voice: Sende an Whisper API")
        with open(tmp_path, "rb") as audio_file:
            whisper_resp = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": ("voice.ogg", audio_file, "audio/ogg")},
                # KEIN festes language=de → Whisper erkennt die Sprache automatisch
                # (Tuerkisch/Arabisch/Englisch …); der Marken-Vocab-Prompt haelt die
                # Produktnamen sprachunabhaengig stabil.
                data={"model": "whisper-1", "prompt": _whisper_vocab_prompt()},
                timeout=30,
            )
            if not whisper_resp.ok:
                log.error(f"Voice: Whisper API error {whisper_resp.status_code}: {whisper_resp.text[:300]}")
                _alert_if_outage(whisper_resp)
                whisper_resp.raise_for_status()
            text = whisper_resp.json().get("text", "").strip()

        os.unlink(tmp_path)
        if _looks_like_no_speech(text):
            log.info(f"Voice (WhatsApp) als No-Speech/Halluzination verworfen: '{text}'")
            return None
        log.info(f"Voice transcribed: '{text}'")
        return text

    except Exception as e:
        log.error(f"WhatsApp voice transcription error: {type(e).__name__}: {e}", exc_info=True)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return None
