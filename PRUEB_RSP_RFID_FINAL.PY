#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import serial, time, json, re, os, sys, subprocess
import sounddevice as sd
import soundfile as sf
import numpy as np
import queue
import unicodedata
from faster_whisper import WhisperModel

# ========= CONFIG =========
PUERTO = "/dev/ttyUSB0"   # o "/dev/serial0" si usas GPIO
BAUD   = 115200

# Archivos (opcionales: si no existen, usamos defaults embebidos)
DIALOG_FILE = "/home/chipi/Desktop/CT/hosvi_dialog.json"
ROUTES_FILE = "/home/chipi/Desktop/CT/hosvi_routes.json"
JSON_TAGS   = "/home/chipi/Desktop/CT/hosvi_tags.json"

# Zona fija (un solo dispositivo en Hall de acceso)
CURRENT_ZONE_NAME = "Zona 2 - Hall de acceso"

# Mensaje/tiempo de espera antes de responder/ejecutar ruta
PROCESSING_DELAY_S = 6

# Audio / ASR
SAMPLERATE = 16000
CHANNELS   = 1
BLOCK_MS   = 25
BLOCKSIZE  = int(SAMPLERATE * (BLOCK_MS / 1000.0))

# VAD por RMS
START_RMS     = 0.010
START_FRAMES  = 3
END_RMS       = 0.004
END_FRAMES    = 12
MAX_SECONDS   = 3.0
MIN_SECONDS   = 0.30

# Whisper local
WHISPER_MODEL_SIZE   = "small"
WHISPER_COMPUTE_TYPE = "int8"

# ========= Estado/colas =========
audio_queue = queue.Queue()
greet_cooldown_s = 5.0         # antirrebote: 5s por EPC
last_greet_by_epc = {}         # epc -> timestamp último saludo

# ========= Regex (formato original SparkFun) =========
# Ejemplo: " rssi[-46] freq[915250] time[123] epc[30 00 11 22 ... ]"
re_epc  = re.compile(r'epc\[\s*([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})+)\s*\]')
re_rssi = re.compile(r'rssi\[\s*(-?\d+)\s*\]')
re_freq = re.compile(r'freq\[\s*(\d+)\s*\]')
re_time = re.compile(r'time\[\s*(\d+)\s*\]')

# ========= Defaults embebidos =========
DIALOG_DEFAULT = {
    "processing_wait": "Dame unos segundos mientras preparo las instrucciones.",
    "repeat_question": "¿Deseas que repita las instrucciones? Responde sí o no.",
    "repeat_ack": "De acuerdo, repetiré las instrucciones.",
    "didnt_understand": "No te entendí. Responde sí o no.",
    "hold_ack": "Me mantendré atento por si necesitas más ayuda.",
    "goodbye": "Gracias. Estoy listo cuando lo necesites."
}

# Rutas por defecto ya alineadas tras el beacon:
# Asumimos que, al finalizar el beacon, el usuario quedó mirando de frente al sonido.
# En esa orientación: DERECHA -> Cafetería, IZQUIERDA -> Enfermería y Portería.
ROUTES_DEFAULT = {
    "options": [
        {
            "question": "¿Deseas guía hacia la cafetería? Responde sí o no.",
            "dest_name": "la cafetería",
            "steps": [
                "Gira a la derecha.",
                "Avanza recto aproximadamente veinte metros siguiendo la pared.",
                "Escucharás mayor actividad y olor a alimentos. Allí está la cafetería."
            ]
        },
        {
            "question": "¿Deseas guía hacia la enfermería? Responde sí o no.",
            "dest_name": "la enfermería",
            "steps": [
                "Gira a la izquierda.",
                "Avanza recto unos quince metros.",
                "La puerta de enfermería estará a tu lado izquierdo, identificada con señal auditiva y táctil."
            ]
        },
        {
            "question": "¿Deseas guía hacia la portería principal? Responde sí o no.",
            "dest_name": "la portería principal",
            "steps": [
                "Gira a la izquierda.",
                "Avanza recto hasta el final del pasillo.",
                "Encontrarás la portería frente a ti, donde se ubica el control de acceso."
            ]
        }
    ]
}

# ========= Utilidades de archivo =========
def load_json(path, default_obj):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default_obj

def load_tags(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # llaves a MAYÚSCULAS y sin espacios
        return {k.strip().upper(): v for k, v in data.items()}
    return {}

DIALOG = load_json(DIALOG_FILE, DIALOG_DEFAULT)
ROUTES = load_json(ROUTES_FILE, ROUTES_DEFAULT)

# ========= ASR =========
print("Cargando modelo faster-whisper...")
CPU_THREADS = max(1, min(4, (os.cpu_count() or 4)))
asr = WhisperModel(
    WHISPER_MODEL_SIZE,
    device="cpu",
    compute_type=WHISPER_COMPUTE_TYPE,
    cpu_threads=CPU_THREADS,
    num_workers=1
)

# ========= Audio helpers =========
def flush_queue(q: queue.Queue):
    try:
        while True:
            q.get_nowait()
    except Exception:
        pass

def sd_callback(indata, frames, time_info, status):
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(indata.copy())

def normalizar_texto(texto: str) -> str:
    texto = texto.lower()
    texto = ''.join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    )
    texto = texto.replace(" ", "")
    return texto.strip()

def tts_pico2wave(texto: str, wav_out="tts_out.wav", idioma="es-ES"):
    try:
        subprocess.run(["pico2wave", f"-l={idioma}", "-w", wav_out, texto], check=True)
    except Exception as e:
        print(f"Error generando voz: {e}")

def hablar(texto: str):
    if not texto:
        return
    wav_out = "tts_out.wav"
    tts_pico2wave(texto, wav_out)
    wav, sr = sf.read(wav_out, dtype="float32")
    sd.play(wav, samplerate=sr)
    sd.wait()

def play_beacon(duration_s=2.5, tone_hz=880.0, beep_ms=250, gap_ms=250):
    """
    Emite un patrón de pitidos para que la persona se oriente y se ubique de frente al sonido.
    """
    fs = SAMPLERATE
    total_ms = int(duration_s * 1000)
    out = []

    t_beep = np.arange(int(fs * (beep_ms / 1000.0)), dtype=np.float32)
    tone = np.sin(2 * np.pi * tone_hz * t_beep / fs).astype(np.float32) * 0.25
    silence = np.zeros(int(fs * (gap_ms / 1000.0)), dtype=np.float32)

    elapsed = 0
    while elapsed < total_ms:
        out.append(tone)
        out.append(silence)
        elapsed += (beep_ms + gap_ms)

    wav = np.concatenate(out) if out else np.array([], dtype=np.float32)
    if wav.size:
        sd.play(wav, samplerate=fs)
        sd.wait()

# ========= ASR: transcripción =========
def transcribir(audio_np: np.ndarray, sr: int) -> str:
    # Asegurar mono float32
    if audio_np.ndim > 1:
        audio_np = audio_np[:, 0]
    audio_np = audio_np.astype(np.float32, copy=False)

    # Remuestreo rápido a 16 kHz si hace falta
    if sr == 16000:
        audio16 = audio_np
    elif sr == 48000:
        audio16 = audio_np[::3]  # 48k -> 16k
    else:
        target_sr = 16000
        n_out = int(round(len(audio_np) * (target_sr / float(sr))))
        if n_out <= 1:
            audio16 = audio_np
        else:
            x_old = np.linspace(0.0, 1.0, num=len(audio_np), endpoint=False, dtype=np.float32)
            x_new = np.linspace(0.0, 1.0, num=n_out,     endpoint=False, dtype=np.float32)
            audio16 = np.interp(x_new, x_old, audio_np).astype(np.float32)

    segments, _ = asr.transcribe(
        audio16,
        language="es",
        vad_filter=False,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        without_timestamps=True,
        condition_on_previous_text=False,
        word_timestamps=False
    )
    texto = "".join(seg.text for seg in segments).strip().lower()
    return texto

# ========= Grabadora con VAD =========
def grabar_utterance() -> np.ndarray:
    flush_queue(audio_queue)
    audio_chunks = []
    speaking = False
    above_count = 0
    silence_count = 0
    start_time = time.time()

    with sd.InputStream(
        samplerate=SAMPLERATE,
        channels=CHANNELS,
        blocksize=BLOCKSIZE,
        callback=sd_callback
    ):
        while True:
            chunk = audio_queue.get()
            rms = float(np.sqrt(np.mean(chunk**2)))

            if not speaking:
                if rms > START_RMS:
                    above_count += 1
                else:
                    above_count = 0
                audio_chunks.append(chunk)

                if above_count >= START_FRAMES:
                    speaking = True
                    silence_count = 0
            else:
                audio_chunks.append(chunk)
                if rms < END_RMS:
                    silence_count += 1
                else:
                    silence_count = 0

                if silence_count >= END_FRAMES:
                    break

            if (time.time() - start_time) > MAX_SECONDS:
                print("Corte por máximo tiempo.")
                break

    if not audio_chunks:
        return np.array([], dtype=np.float32)
    audio = np.concatenate(audio_chunks, axis=0)
    duration = len(audio) / SAMPLERATE
    if duration < MIN_SECONDS:
        print(f"Demasiado corto ({duration:.2f}s).")
        return np.array([], dtype=np.float32)
    return audio.astype(np.float32)

# ========= Helpers de diálogo =========
def say(key, **kwargs):
    txt = DIALOG.get(key, "")
    if kwargs:
        try:
            txt = txt.format(**kwargs)
        except Exception:
            pass
    if not txt:
        return
    hablar(txt)

def ask_yes_no(prompt) -> str:
    """
    Pregunta cerrada con la indicación: 'Responde sí o no.'
    """
    hablar(f"{prompt} Responde sí o no.")
    audio = grabar_utterance()
    if audio.size == 0:
        return ""

    t0 = time.time()
    texto_raw = transcribir(audio, SAMPLERATE)
    print(f"[TIMING] transcribir> {time.time()-t0:.2f}s")
    print(f"[ASR] ask_yes_no -> '{texto_raw}'")
    t = normalizar_texto(texto_raw)
    print(f"[ASR-NORM] ask_yes_no -> '{t}'")

    if "si" in t or "sí" in t or "claro" in t or "deuna" in t or "dale" in t:
        return "si"
    if "no" in t:
        return "no"
    return t  # texto libre (ej. 'salir')

def run_steps(steps: list):
    # Aviso de procesamiento y pausa breve
    hablar(DIALOG.get("processing_wait", "Dame unos segundos."))
    time.sleep(PROCESSING_DELAY_S)

    # 1ra pasada
    for s in steps:
        hablar(s)
        time.sleep(0.3)

    # Aviso y 2da pasada automática
    hablar("Repetiré las instrucciones una vez más.")
    for s in steps:
        hablar(s)
        time.sleep(0.3)

    # Pregunta si repetir otra vez (a petición)
    say("repeat_question")
    audio = grabar_utterance()
    if audio.size == 0:
        return

    texto_raw = transcribir(audio, SAMPLERATE)
    print(f"[ASR] repeat_question -> '{texto_raw}'")
    t = normalizar_texto(texto_raw)
    print(f"[ASR-NORM] repeat_question -> '{t}'")

    if "si" in t or "sí" in t or "repite" in t or "otravez" in t or "otra vez" in t:
        say("repeat_ack")
        for s in steps:
            hablar(s)
            time.sleep(0.3)

# ========= Lógica principal =========
def bienvenida_y_beacon(name=None):
    # 1) Bienvenida
    if name:
	    hablar(f"Hola {name}, mi nombre es HOSVI y estoy aqui para ayudarte a orientar dentro de la Universidad Santo Tomás.")
    else:
	    hablar("Bienvenida, persona con discapacidad visual, a la Universidad Santo Tomás. Estoy aquí para ayudarte a orientarte.")
		
    hablar(f"Te encuentras en {CURRENT_ZONE_NAME}.")
    # 2) Beacon/sonido guía
    hablar("Emitiré un sonido de referencia. Por favor, teniendo en cuenta el sonido que escuchaste, posiciónate en frente de ese sonido.")
    play_beacon(duration_s=3.0, tone_hz=880.0, beep_ms=250, gap_ms=250)
    # 3) Confirmación breve
    hablar("Perfecto. A partir de esta orientación, hacia tu derecha está la cafetería, y hacia tu izquierda están la enfermería y la portería.")

def main():
    tags_map = load_tags(JSON_TAGS)
    print(f"[INFO] {len(tags_map)} EPC mapeados cargados desde {JSON_TAGS}")

    with serial.Serial(PUERTO, baudrate=BAUD, timeout=1) as arduino:
        time.sleep(2)  # estabiliza conexión
        print("[INFO] Conectado. Esperando solicitud de tecla...")

        while True:
            line = arduino.readline().decode(errors="ignore").strip()
            if not line:
                continue

            # Handshake del ejemplo SparkFun
            if line == "Press a key to begin scanning for tags.":
                arduino.write(b"A")
                print("[OK] Handshake enviado. Listo para leer tags.")
                continue

            # Mensajes informativos del sketch original
            if line in ("Scanning",):
                continue
            if line in ("High return loss, check antenna!", "Bad CRC", "Unknown error", "Module failed to respond. Please check wiring."):
                print(f"[INFO] {line}")
                continue

            # Parsear EPC
            m_epc = re_epc.search(line)
            if not m_epc:
                print(f"[RAW] {line}")
                continue

            epc_bytes = m_epc.group(1)               # "30 00 11 22 ..."
            epc = "".join(epc_bytes.split()).upper() # "30001122..."

            # (Opcional) logs
            m_rssi = re_rssi.search(line); rssi = m_rssi.group(1) if m_rssi else "NA"
            m_freq = re_freq.search(line); freq = m_freq.group(1) if m_freq else "NA"
            m_time = re_time.search(line); ts   = m_time.group(1) if m_time else "NA"

            name = tags_map.get(epc, None)
            if name is None:
                print(f"TAG: name=UNKNOWN epc={epc} rssi={rssi} freq={freq} ts={ts}")
                continue

            print(f"TAG: name={name} epc={epc} rssi={rssi} freq={freq} ts={ts}")

            # Antirrebote por EPC
            now = time.time()
            last = last_greet_by_epc.get(epc, 0)
            if now - last < greet_cooldown_s:
                continue
            last_greet_by_epc[epc] = now

            # ─── Bienvenida y beacon (NUEVO flujo) ─────────────────────────────────
            bienvenida_y_beacon(name)

            # ─── Menú de rutas (ya alineadas a la nueva orientación) ───────────────
            options = ROUTES.get("options", [])
            if not options:
                hablar("Aún no tengo rutas configuradas para esta zona.")
                hablar(DIALOG.get("goodbye", "Gracias."))
                time.sleep(8)
                continue

            chose_something = False
            for opt in options:
                resp = ask_yes_no(opt.get("question", "¿Deseas guía?"))
                if resp == "si":
                    hablar(f"Iniciaremos la guía hacia {opt.get('dest_name','el destino')}.")
                    run_steps(opt.get("steps", []))
                    hablar(DIALOG.get("goodbye", "Gracias."))
                    time.sleep(8)
                    chose_something = True
                    break
                elif resp == "no":
                    continue
                elif "salir" in resp:
                    hablar(DIALOG.get("goodbye", "Gracias."))
                    time.sleep(8)
                    chose_something = True
                    break
                else:
                    # No entendí; reintento una vez
                    hablar(DIALOG.get("didnt_understand", "No te entendí."))
                    resp2 = ask_yes_no(opt.get("question", "¿Deseas guía?"))
                    if resp2 == "si":
                        hablar(f"Iniciaremos la guía hacia {opt.get('dest_name','el destino')}.")
                        run_steps(opt.get("steps", []))
                        hablar(DIALOG.get("goodbye", "Gracias."))
                        time.sleep(8)
                        chose_something = True
                        break
                    elif resp2 == "no":
                        continue
                    elif "salir" in resp2:
                        hablar(DIALOG.get("goodbye", "Gracias."))
                        time.sleep(8)
                        chose_something = True
                        break

            # Si dijo "no" a todas las opciones (o nunca eligió), cierre amable
            if not chose_something:
                hablar(DIALOG.get("hold_ack", "Me mantendré atento."))
                hablar(DIALOG.get("goodbye", "Gracias."))
                time.sleep(8)

# ========= Entrypoint =========
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Saliendo…")
    finally:
        for f in ["tts_out.wav", "temp_input.wav"]:
            try:
                os.remove(f)
            except Exception:
                pass