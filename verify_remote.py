import sys
import time

# Reconfigure stdout for UTF-8 just in case
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def test_imports():
    print("Checking libraries...")
    
    try:
        import fastapi
        print("OK: fastapi version:", fastapi.__version__)
    except ImportError as e:
        print("FAIL: fastapi failed to import:", e)
        return False
        
    try:
        import uvicorn
        print("OK: uvicorn version:", uvicorn.__version__)
    except ImportError as e:
        print("FAIL: uvicorn failed to import:", e)
        return False
        
    try:
        import pyautogui
        print("OK: pyautogui resolution:", pyautogui.size())
    except Exception as e:
        print("FAIL: pyautogui check failed:", e)
        return False
        
    try:
        import mss
        print("OK: mss version:", mss.__version__)
    except ImportError as e:
        print("FAIL: mss failed to import:", e)
        return False
        
    try:
        import pyaudiowpatch as pyaudio
        p = pyaudio.PyAudio()
        print("OK: pyaudiowpatch successfully initialized PortAudio.")
        p.terminate()
    except Exception as e:
        print("FAIL: pyaudiowpatch failed:", e)
        return False
        
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (100, 100))
        print("OK: pillow Image created successfully.")
    except Exception as e:
        print("FAIL: pillow check failed:", e)
        return False
        
    return True

def test_port_binding():
    print("Testing port 3350 binding...")
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 3350))
        print("OK: Port 3350 is free and bindable.")
        s.close()
        return True
    except Exception as e:
        print("FAIL: Port 3350 binding failed:", e)
        s.close()
        return False

if __name__ == "__main__":
    print("=== Remote Desktop System Verification ===")
    imports_ok = test_imports()
    port_ok = test_port_binding()
    
    if imports_ok and port_ok:
        print("\n*** ALL CHECKS PASSED SUCCESSFULLY! ***")
        sys.exit(0)
    else:
        print("\n*** SOME CHECKS FAILED. ***")
        sys.exit(1)
