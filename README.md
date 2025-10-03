# 🦾 HOSVI – Hospital Smart Voice Interface

**HOSVI** (Hospital Smart Voice Interface) es un sistema de orientación por voz pensado para personas con discapacidad visual.  
Este prototipo utiliza **RFID UHF**, **ASR (Reconocimiento de voz con Whisper)** y **síntesis de voz** para guiar a los usuarios dentro de un entorno hospitalario o universitario, brindando autonomía y accesibilidad.

---

## 📂 Archivos principales

- `PRUEB_RSP_RFID_FINAL.PY` → Código principal en Python que integra:
  - Lectura de **tags RFID UHF** vía puerto serial.
  - Reconocimiento de voz con **faster-whisper**.
  - Reproducción de audio con **pico2wave** y `sounddevice`.
  - Lógica de interacción (bienvenida, beacon, menú de rutas).
- `HOSVI_DIALOG.JSON` → Contiene los diálogos del asistente (mensajes de bienvenida, confirmaciones, despedida, etc.).
- `HOSVI_ROUTES.JSON` → Define las rutas disponibles desde cada zona (preguntas, destino y pasos detallados).
- `HOSVI_TAGS.JSON` → Mapea los **EPC** de las tarjetas RFID con los nombres de los usuarios.

---

## ⚙️ Instalación

### 1. Clonar el repositorio
```bash
git clone https://github.com/tuusuario/HOSVI.git
cd HOSVI
```
### 2. Crear entorno virtual
```bash
python3 -m venv hosvi
source hosvi/bin/activate
```
### 3. Instalar dependencias
```bash
pip install sounddevice soundfile numpy faster-whisper pyserial
```
## 🚀 Uso
### 1. Conecta el lector RFID UHF en el puerto serial configurado (/dev/ttyUSB0 o /dev/serial0).
### 2. Ejecuta el programa:
```bash
python PRUEB_RSP_RFID_FINAL.PY
```
### 3. Al detectar una tarjeta RFID:
- El sistema identifica al usuario (ejemplo: Juanes).
- Da la bienvenida personalizada.
- Emite un beacon sonoro para orientación inicial.
- Ofrece las rutas disponibles según la zona (cafetería, enfermería, portería).
- Guía paso a paso con instrucciones habladas.

## 🗺️ Ejemplo de interacción
- Usuario se acerca con su tarjeta.
- HOSVI: "Hola Juanes, mi nombre es HOSVI y estoy aquí para ayudarte. Te encuentras en la Zona 2 - Hall de acceso."
- Se reproduce el beacon 🔊.
- HOSVI: "¿Deseas guía hacia la cafetería? Responde sí o no."
- Usuario: "Sí"
- HOSVI: "Perfecto, iniciaremos la guía hacia la cafetería. Gira a la derecha, avanza recto quince pasos..."

## 👥 Autores

Proyecto desarrollado por:

- **Juan Esteban Páez Fonseca (Juanes)**
- **Daniela Alejandra Reyex Ballen**
- **Yuli Andrea Alvarez Pizarro**
- **Oscar Fernando Becerra Angarita**

Universidad Santo Tomás – Bucaramanga
