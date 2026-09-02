import os
import sys
import io
import time
import json
import asyncio
import threading
from typing import Dict, Any
import pyautogui
import mss
from PIL import Image, ImageDraw
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController

import websockets

# Force UTF-8 on Windows terminal
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Simulators
mouse = MouseController()
keyboard = KeyboardController()
screen_width, screen_height = pyautogui.size()
pyautogui.PAUSE = 0.0
pyautogui.FAILSAFE = False

# Keyboard map for special keys from browser events (code or key)
KEY_MAP = {
    "Enter": Key.enter,
    "Backspace": Key.backspace,
    "Tab": Key.tab,
    "Space": Key.space,
    "Escape": Key.esc,
    "ShiftLeft": Key.shift_l,
    "ShiftRight": Key.shift_r,
    "ControlLeft": Key.ctrl_l,
    "ControlRight": Key.ctrl_r,
    "AltLeft": Key.alt_l,
    "AltRight": Key.alt_r,
    "MetaLeft": Key.cmd_l,
    "MetaRight": Key.cmd_r,
    "ArrowUp": Key.up,
    "ArrowDown": Key.down,
    "ArrowLeft": Key.left,
    "ArrowRight": Key.right,
    "PageUp": Key.page_up,
    "PageDown": Key.page_down,
    "Home": Key.home,
    "End": Key.end,
    "Insert": Key.insert,
    "Delete": Key.delete,
    "CapsLock": Key.caps_lock,
    "F1": Key.f1,
    "F2": Key.f2,
    "F3": Key.f3,
    "F4": Key.f4,
    "F5": Key.f5,
    "F6": Key.f6,
    "F7": Key.f7,
    "F8": Key.f8,
    "F9": Key.f9,
    "F10": Key.f10,
    "F11": Key.f11,
    "F12": Key.f12,
}

# Default runtime settings (controlled by viewer settings)
settings = {
    "quality": 60,
    "scale": 1.0,
    "fps": 30,
    "audio_enabled": True,
}

running = True
websocket_conn = None

# Fallback dummy frame
def get_dummy_frame(width: int, height: int, text: str) -> bytes:
    img = Image.new("RGB", (width, height), color=(24, 24, 28))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, width - 10, height - 10], outline=(40, 40, 50), width=2)
    draw.text((width // 2 - 180, height // 2 - 10), text, fill=(180, 180, 200))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=40)
    return out.getvalue()

DUMMY_FRAME = get_dummy_frame(1280, 720, "Screen capturing unavailable in this session.\nPlease run in an active user session.")

# Input Execution
def execute_control(event: Dict[str, Any]):
    ev_type = event.get("type")
    
    if ev_type == "mouse_move":
        x = event.get("x", 0.0)
        y = event.get("y", 0.0)
        target_x = int(x * screen_width)
        target_y = int(y * screen_height)
        pyautogui.moveTo(target_x, target_y)
        
    elif ev_type == "mouse_click":
        btn = event.get("button", "left")
        action = event.get("action", "click")
        if action == "click":
            pyautogui.click(button=btn)
        elif action == "double":
            pyautogui.doubleClick(button=btn)
        elif action == "down":
            pyautogui.mouseDown(button=btn)
        elif action == "up":
            pyautogui.mouseUp(button=btn)
            
    elif ev_type == "mouse_scroll":
        dx = event.get("dx", 0)
        dy = event.get("dy", 0)
        pyautogui.scroll(dy)
        
    elif ev_type == "key_event":
        key_name = event.get("key", "")
        code = event.get("code", "")
        action = event.get("action", "down")
        
        special_key = KEY_MAP.get(code) or KEY_MAP.get(key_name)
        if special_key:
            try:
                if action == "down":
                    keyboard.press(special_key)
                else:
                    keyboard.release(special_key)
            except Exception as e:
                print(f"Special key simulation failed ({code}): {e}")
        else:
            if len(key_name) == 1:
                try:
                    if action == "down":
                        keyboard.press(key_name)
                    else:
                        keyboard.release(key_name)
                except Exception as e:
                    print(f"Char key simulation failed ({key_name}): {e}")

# Audio Loopback Device Resolver
def get_loopback_device_info(p):
    try:
        wasapi_info = p.get_host_api_info_by_type(13) # paWASAPI = 13
    except Exception:
        wasapi_info = None
        for i in range(p.get_host_api_count()):
            api_info = p.get_host_api_info_by_index(i)
            if "WASAPI" in api_info["name"]:
                wasapi_info = api_info
                break
                
    if not wasapi_info:
        return None
        
    default_output = wasapi_info["defaultOutputDevice"]
    if default_output < 0:
        for i in range(p.get_device_count()):
            dev_info = p.get_device_info_by_index(i)
            if dev_info["hostApi"] == wasapi_info["index"] and dev_info["maxOutputChannels"] > 0:
                default_output = i
                break
                
    if default_output < 0:
        return None
        
    default_out_info = p.get_device_info_by_index(default_output)
    default_out_name = default_out_info["name"]
    
    for i in range(p.get_device_count()):
        dev_info = p.get_device_info_by_index(i)
        if (dev_info["hostApi"] == wasapi_info["index"] and 
            dev_info.get("isLoopbackDevice", False) and 
            default_out_name in dev_info["name"]):
            return dev_info
            
    for i in range(p.get_device_count()):
        dev_info = p.get_device_info_by_index(i)
        if dev_info["hostApi"] == wasapi_info["index"] and dev_info.get("isLoopbackDevice", False):
            return dev_info
            
    for i in range(p.get_device_count()):
        dev_info = p.get_device_info_by_index(i)
        if dev_info["hostApi"] == wasapi_info["index"] and "[Loopback]" in dev_info["name"]:
            return dev_info
            
    return None

# Audio Thread Function
def audio_capture_thread_func(loop):
    import pyaudiowpatch as pyaudio
    
    p = pyaudio.PyAudio()
    stream = None
    try:
        dev_info = get_loopback_device_info(p)
        if not dev_info:
            print("No WASAPI loopback device found. Audio streaming disabled.")
            return
            
        print(f"Capturing audio from: {dev_info['name']}")
        channels = dev_info['maxInputChannels']
        rate = int(dev_info['defaultSampleRate'])
        
        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=dev_info['index'],
            frames_per_buffer=1024
        )
        
        while running:
            if not websocket_conn or not settings.get("audio_enabled", True):
                time.sleep(0.1)
                continue
                
            try:
                data = stream.read(1024, exception_on_overflow=False)
                if data:
                    # Package format: 2 (audio type) + raw bytes
                    payload = bytes([2]) + data
                    loop.call_soon_threadsafe(asyncio.create_task, send_binary(payload))
            except Exception:
                time.sleep(0.1)
    except Exception as e:
        print(f"Audio Thread Error: {e}")
    finally:
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        p.terminate()

# Safe Threaded Binary Message Sending
async def send_binary(payload: bytes):
    global websocket_conn
    if websocket_conn:
        try:
            await websocket_conn.send(payload)
        except Exception:
            pass

# Screen capture loop
async def screen_capture_loop():
    global websocket_conn
    print("Screen capture task started.")
    sct = None
    try:
        sct = mss.mss()
    except Exception as e:
        print(f"Failed to initialize mss: {e}")

    while running:
        t_start = asyncio.get_event_loop().time()
        
        if not websocket_conn:
            await asyncio.sleep(0.2)
            continue
            
        fps = settings.get("fps", 30)
        interval = 1.0 / max(1, fps)
        
        frame_bytes = None
        if sct:
            try:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                
                scale = settings.get("scale", 1.0)
                if scale != 1.0:
                    new_size = (int(img.width * scale), int(img.height * scale))
                    img = img.resize(new_size, Image.Resampling.BILINEAR)
                
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=settings.get("quality", 60))
                frame_bytes = out.getvalue()
            except Exception as e:
                frame_bytes = DUMMY_FRAME
        else:
            frame_bytes = DUMMY_FRAME

        if frame_bytes and websocket_conn:
            try:
                # Package format: 1 (screen type) + raw JPEG bytes
                payload = bytes([1]) + frame_bytes
                await websocket_conn.send(payload)
            except Exception as e:
                pass

        t_elapsed = asyncio.get_event_loop().time() - t_start
        sleep_time = max(0.005, interval - t_elapsed)
        await asyncio.sleep(sleep_time)

    if sct:
        sct.close()

# Connection loop
async def connect_loop(host_ip: str, port: int, passcode: str):
    global websocket_conn, running
    
    # Resolve audio format details before connecting
    import pyaudiowpatch as pyaudio
    p = pyaudio.PyAudio()
    dev_info = get_loopback_device_info(p)
    audio_info = None
    if dev_info:
        audio_info = {
            "channels": dev_info['maxInputChannels'],
            "samplerate": int(dev_info['defaultSampleRate'])
        }
    p.terminate()

    ws_url = f"ws://{host_ip}:{port}/agent_ws?passcode={passcode}"
    
    while running:
        print(f"Connecting to home controller: {ws_url}...")
        try:
            async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
                print("Successfully connected to home controller!")
                websocket_conn = ws
                
                # Send initialization metadata
                init_msg = {
                    "screen_width": screen_width,
                    "screen_height": screen_height,
                    "audio": audio_info
                }
                await ws.send(json.dumps(init_msg))
                
                # Listen for commands
                async for message in ws:
                    event = json.loads(message)
                    
                    if event.get("type") == "settings":
                        settings["quality"] = event.get("quality", settings["quality"])
                        settings["scale"] = event.get("scale", settings["scale"])
                        settings["fps"] = event.get("fps", settings["fps"])
                        settings["audio_enabled"] = event.get("audio_enabled", settings["audio_enabled"])
                        print(f"Updated settings from controller: {settings}")
                    else:
                        execute_control(event)
                        
        except Exception as e:
            print(f"Connection lost or failed: {e}")
            websocket_conn = None
            
        print("Reconnecting in 3 seconds...")
        await asyncio.sleep(3)

def main():
    # Read target IP from config.txt, or request it
    config_file = "config.txt"
    target_ip = "127.0.0.1"
    passcode = "3350"
    
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                lines = f.read().splitlines()
                if len(lines) > 0:
                    target_ip = lines[0].strip()
                if len(lines) > 1:
                    passcode = lines[1].strip()
            print(f"Loaded config: target={target_ip}, passcode={'*' * len(passcode)}")
        except Exception as e:
            print("Failed to read config.txt:", e)
    else:
        # Create default config.txt
        try:
            with open(config_file, "w") as f:
                f.write("127.0.0.1\n3350\n")
            print("Created default config.txt (IP: 127.0.0.1, passcode: 3350)")
        except Exception:
            pass
            
    print("--------------------------------------------------")
    print(f"Reverse Agent Mode: Controlled Device")
    print(f"Targeting Home Controller: {target_ip}:3350")
    print("--------------------------------------------------")
    
    # Launch async loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Start screen loop task
    loop.create_task(screen_capture_loop())
    
    # Start audio capture thread
    threading.Thread(target=audio_capture_thread_func, args=(loop,), daemon=True).start()
    
    # Run connection loop
    try:
        loop.run_until_complete(connect_loop(target_ip, 3350, passcode))
    except KeyboardInterrupt:
        global running
        running = False
        print("Agent shutting down...")

if __name__ == "__main__":
    main()
