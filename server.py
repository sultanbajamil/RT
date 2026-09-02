import os
import sys
import io
import time
import json
import asyncio
import threading
from typing import Dict, List, Any
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import pyautogui
import mss
from PIL import Image, ImageDraw
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController

# Force UTF-8 on Windows terminal for logging output
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

app = FastAPI(title="Remote Control Server")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Disable pyautogui pauses for maximum speed
pyautogui.PAUSE = 0.0
pyautogui.FAILSAFE = False

# Simulators
mouse = MouseController()
keyboard = KeyboardController()
screen_width, screen_height = pyautogui.size()

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

# Passcode for security
PASSCODE = "3350"

class ClientConnection:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.queue = asyncio.Queue(maxsize=120)
        self.task = None

    async def send_loop(self):
        try:
            while True:
                msg_type, data = await self.queue.get()
                # Package format: Type (1 byte) + Data (remaining bytes)
                payload = bytes([msg_type]) + data
                await self.websocket.send_bytes(payload)
                self.queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Send loop error: {e}")

    def push_message(self, msg_type: int, data: bytes):
        try:
            if self.queue.full():
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                except asyncio.QueueEmpty:
                    pass
            self.queue.put_nowait((msg_type, data))
        except asyncio.QueueFull:
            pass

class ConnectionManager:
    def __init__(self):
        self.connections: List[ClientConnection] = []
        self.agent_websocket: WebSocket = None
        self.agent_info: Dict[str, Any] = {}
        self.running = True
        self.settings = {
            "quality": 60,
            "scale": 1.0,
            "fps": 30,
            "audio_enabled": True,
        }
        self.audio_settings = {
            "channels": 2,
            "samplerate": 48000
        }

    async def connect(self, websocket: WebSocket) -> ClientConnection:
        conn = ClientConnection(websocket)
        conn.task = asyncio.create_task(conn.send_loop())
        self.connections.append(conn)
        print(f"Viewer connected. Active viewers: {len(self.connections)}")
        return conn

    def disconnect(self, conn: ClientConnection):
        if conn in self.connections:
            self.connections.remove(conn)
            if conn.task:
                conn.task.cancel()
            print(f"Viewer disconnected. Active viewers: {len(self.connections)}")

    def broadcast(self, msg_type: int, data: bytes):
        for conn in self.connections:
            conn.push_message(msg_type, data)

manager = ConnectionManager()

# --- Fallback Screen Frame Generator ---
def get_dummy_frame(width: int, height: int, text: str) -> bytes:
    img = Image.new("RGB", (width, height), color=(24, 24, 28))
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, width - 10, height - 10], outline=(40, 40, 50), width=2)
    draw.text((width // 2 - 180, height // 2 - 10), text, fill=(180, 180, 200))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=40)
    return out.getvalue()

DUMMY_FRAME = get_dummy_frame(1280, 720, "Screen capturing unavailable in this session.\nPlease run in an active user session.")

# --- Screen Capture Loop (Direct Mode Only) ---
async def screen_capture_loop():
    print("Screen capture task started.")
    sct = None
    try:
        sct = mss.mss()
    except Exception as e:
        print(f"Failed to initialize mss: {e}")

    while manager.running:
        t_start = asyncio.get_event_loop().time()
        
        # If no clients or if we are in Reverse Mode, pause local capture
        if not manager.connections or manager.agent_websocket is not None:
            await asyncio.sleep(0.2)
            continue
            
        fps = manager.settings.get("fps", 30)
        interval = 1.0 / max(1, fps)
        
        frame_bytes = None
        if sct:
            try:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                
                scale = manager.settings.get("scale", 1.0)
                if scale != 1.0:
                    new_size = (int(img.width * scale), int(img.height * scale))
                    img = img.resize(new_size, Image.Resampling.BILINEAR)
                
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=manager.settings.get("quality", 60))
                frame_bytes = out.getvalue()
            except Exception as e:
                frame_bytes = DUMMY_FRAME
        else:
            frame_bytes = DUMMY_FRAME

        if frame_bytes:
            manager.broadcast(1, frame_bytes)

        t_elapsed = asyncio.get_event_loop().time() - t_start
        sleep_time = max(0.005, interval - t_elapsed)
        await asyncio.sleep(sleep_time)

    if sct:
        sct.close()

# --- Audio Capture (Direct Mode Only) ---
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

def audio_capture_thread_func(loop):
    import pyaudiowpatch as pyaudio
    
    p = pyaudio.PyAudio()
    stream = None
    try:
        dev_info = get_loopback_device_info(p)
        if not dev_info:
            print("No WASAPI loopback device found. Audio streaming disabled.")
            return
            
        print(f"Streaming audio from: {dev_info['name']}")
        channels = dev_info['maxInputChannels']
        rate = int(dev_info['defaultSampleRate'])
        
        # Save local settings
        manager.audio_settings = {
            "channels": channels,
            "samplerate": rate
        }
        
        stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=dev_info['index'],
            frames_per_buffer=1024
        )
        
        while manager.running:
            # If in Reverse Mode, do not record locally
            if manager.agent_websocket is not None:
                time.sleep(0.2)
                continue
                
            if not manager.connections or not manager.settings.get("audio_enabled", True):
                time.sleep(0.1)
                continue
                
            try:
                data = stream.read(1024, exception_on_overflow=False)
                if data:
                    loop.call_soon_threadsafe(manager.broadcast, 2, data)
            except Exception as e:
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

# --- Keyboard and Mouse Controllers ---
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

# --- API Endpoints ---
@app.get("/")
def read_root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    passcode = websocket.query_params.get("passcode")
    if passcode != PASSCODE:
        await websocket.close(code=4001, reason="Invalid Passcode")
        return

    await websocket.accept()
    conn = await manager.connect(websocket)
    
    # Send init info
    if manager.agent_websocket:
        init_info = {
            "type": "init",
            "screen_width": manager.agent_info.get("screen_width", screen_width),
            "screen_height": manager.agent_info.get("screen_height", screen_height),
            "audio": manager.agent_info.get("audio", manager.audio_settings),
            "mode": "reverse"
        }
    else:
        init_info = {
            "type": "init",
            "screen_width": screen_width,
            "screen_height": screen_height,
            "audio": manager.audio_settings,
            "mode": "direct"
        }
        
    await websocket.send_text(json.dumps(init_info))
    
    try:
        while True:
            data = await websocket.receive_text()
            event = json.loads(data)
            
            # Ping-Pong routing
            if event.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue
                
            # Routing events
            if manager.agent_websocket:
                # Reverse Mode: Send viewer inputs to the active reverse agent
                try:
                    await manager.agent_websocket.send_text(data)
                except Exception as e:
                    print(f"Failed to forward message to agent: {e}")
            else:
                # Direct Mode: Execute locally
                if event.get("type") == "settings":
                    manager.settings["quality"] = event.get("quality", manager.settings["quality"])
                    manager.settings["scale"] = event.get("scale", manager.settings["scale"])
                    manager.settings["fps"] = event.get("fps", manager.settings["fps"])
                    manager.settings["audio_enabled"] = event.get("audio_enabled", manager.settings["audio_enabled"])
                    print(f"Updated stream settings: {manager.settings}")
                else:
                    execute_control(event)
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket processing error: {e}")
    finally:
        manager.disconnect(conn)

@app.websocket("/agent_ws")
async def agent_websocket_endpoint(websocket: WebSocket):
    passcode = websocket.query_params.get("passcode")
    if passcode != PASSCODE:
        await websocket.close(code=4001, reason="Invalid Passcode")
        print("Agent rejected: Invalid passcode")
        return

    await websocket.accept()
    print("Reverse Agent connected!")
    manager.agent_websocket = websocket
    
    # Read the agent's initialization info (resolution and audio spec)
    try:
        init_data = await websocket.receive_text()
        manager.agent_info = json.loads(init_data)
        print("Received reverse agent metadata:", manager.agent_info)
        
        # Broadcast connection update to any connected web viewer
        for conn in manager.connections:
            update_info = {
                "type": "init",
                "screen_width": manager.agent_info.get("screen_width", screen_width),
                "screen_height": manager.agent_info.get("screen_height", screen_height),
                "audio": manager.agent_info.get("audio", manager.audio_settings),
                "mode": "reverse"
            }
            await conn.websocket.send_text(json.dumps(update_info))
            
    except Exception as e:
        print(f"Failed to receive agent metadata: {e}")
        await websocket.close()
        manager.agent_websocket = None
        return

    try:
        while True:
            # Read binary frames (1 byte type + data payload) from agent
            data = await websocket.receive_bytes()
            if len(data) > 1:
                msg_type = data[0]
                payload = data[1:]
                # Forward to all connected viewers
                manager.broadcast(msg_type, payload)
    except WebSocketDisconnect:
        print("Reverse Agent disconnected.")
    except Exception as e:
        print(f"Agent websocket loop error: {e}")
    finally:
        manager.agent_websocket = None
        manager.agent_info = {}
        # Notify remaining viewers that we reverted to direct mode
        for conn in manager.connections:
            revert_info = {
                "type": "init",
                "screen_width": screen_width,
                "screen_height": screen_height,
                "audio": manager.audio_settings,
                "mode": "direct"
            }
            try:
                await conn.websocket.send_text(json.dumps(revert_info))
            except Exception:
                pass

@app.on_event("startup")
async def startup_event():
    manager.running = True
    asyncio.create_task(screen_capture_loop())
    loop = asyncio.get_running_loop()
    threading.Thread(target=audio_capture_thread_func, args=(loop,), daemon=True).start()

@app.on_event("shutdown")
def shutdown_event():
    manager.running = False

if __name__ == "__main__":
    print(f"Starting Remote Control Server on port 3350...")
    print(f"Open http://localhost:3350 in your browser to test.")
    print(f"Local network access: http://<your-ip>:3350")
    uvicorn.run("server:app", host="0.0.0.0", port=3350, log_level="info")
