"""
app.py
WSI Viewer — FastAPI tile server with WebSocket relay.

Usage:
    python app.py <slide_path> [--port PORT] [--host HOST] [--tile-size SIZE]

Example:
    python app.py C:\\slides\\CMU-1.svs --port 8000
"""

import os
import sys
import atexit
import argparse
from io import BytesIO

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from wsi_reader import WSIReader


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="WSI Tile Viewer Server")
    parser.add_argument("slide", help="Path to WSI file (.svs, .ndpi, .tiff …)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--tile-size", type=int, default=256)
    return parser.parse_args()


args = parse_args()

# ---------------------------------------------------------------------------
# Slide reader
# ---------------------------------------------------------------------------

print("[viewer] Loading slide: {}".format(args.slide))
try:
    reader = WSIReader(args.slide, tile_size=args.tile_size)
except Exception as e:
    print("[viewer] ERROR: Could not open slide — {}".format(e))
    sys.exit(1)

atexit.register(reader.close)

info = reader.get_info()
print("[viewer]   Dimensions : {}".format(info["slide_dimensions"]))
print("[viewer]   Objective  : {}".format(info["objective_power"]))
print("[viewer]   DZ levels  : {}".format(info["dz_level_count"]))
print("[viewer]   Tile size  : {}".format(info["tile_size"]))

# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(title="WSI Viewer")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


# ---- WebSocket connection manager ----

class ConnectionManager:
    def __init__(self):
        self.connections = []          # list of WebSocket

    async def connect(self, ws):
        await ws.accept()
        self.connections.append(ws)
        print("[ws] Client connected  (total: {})".format(len(self.connections)))

    def disconnect(self, ws):
        if ws in self.connections:
            self.connections.remove(ws)
        print("[ws] Client disconnected (total: {})".format(len(self.connections)))

    async def broadcast(self, message, sender=None):
        """Send to all clients except the sender."""
        dead = []
        for conn in self.connections:
            if conn is sender:
                continue
            try:
                await conn.send_json(message)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)


manager = ConnectionManager()


# ---- Routes ----

@app.get("/", response_class=HTMLResponse)
async def root():
    path = os.path.join(STATIC_DIR, "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/slide/info")
async def slide_info():
    return JSONResponse(reader.get_info())


@app.get("/tiles/{level}/{col}/{row}.jpeg")
async def get_tile(level: int, col: int, row: int):
    try:
        tile = reader.get_tile(level, col, row)
    except ValueError:
        return Response(status_code=404)
    except Exception as e:
        return Response(status_code=500, content=str(e))

    buf = BytesIO()
    tile.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            # Relay to every other connected client
            await manager.broadcast(data, sender=ws)
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as e:
        print("[ws] Error: {}".format(e))
        manager.disconnect(ws)


# Static files (mounted AFTER explicit routes so they don't shadow them)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("[viewer] Starting at http://{}:{}".format(args.host, args.port))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")