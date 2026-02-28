/**
 * viewer.js
 * WSI Viewer frontend — OpenSeadragon + HUD + WebSocket + Gaze overlay
 * v2 — zoom-adaptive sigma, gaze render modes, keyboard shortcuts
 */
(function () {
    "use strict";

    // ---------------------------------------------------------------
    // State
    // ---------------------------------------------------------------
    var slideInfo  = null;
    var viewer     = null;
    var ws         = null;
    var gazePoints = [];
    var gazeCanvas = null;
    var gazeCtx    = null;

    var MAX_GAZE   = 200;
    var FADE_MS    = 800;

    // Gaze render mode: "all" | "latest" | "off"
    var gazeMode = "all";

    // ---------------------------------------------------------------
    // Boot
    // ---------------------------------------------------------------

    function boot() {
        fetch("/slide/info")
            .then(function (r) { return r.json(); })
            .then(function (info) {
                slideInfo = info;
                document.getElementById("hud-file").textContent =
                    "File: " + info.filename;
                initViewer();
                initGazeCanvas();
                initWebSocket();
                initKeyboard();
                requestAnimationFrame(renderLoop);
            })
            .catch(function (err) {
                console.error("Failed to load slide info:", err);
            });
    }

    // ---------------------------------------------------------------
    // OpenSeadragon
    // ---------------------------------------------------------------

    function initViewer() {
        var maxLevel = slideInfo.dz_max_level;
        var maxDims  = slideInfo.dz_max_dimensions;

        viewer = OpenSeadragon({
            id: "openseadragon",
            prefixUrl:
                "https://cdn.jsdelivr.net/npm/openseadragon@4.1.1/" +
                "build/openseadragon/images/",
            tileSources: {
                width:    maxDims[0],
                height:   maxDims[1],
                tileSize: slideInfo.tile_size,
                maxLevel: maxLevel,
                minLevel: 0,
                getTileUrl: function (level, x, y) {
                    return "/tiles/" + level + "/" + x + "/" + y + ".jpeg";
                }
            },
            showNavigator:        true,
            navigatorPosition:    "BOTTOM_RIGHT",
            navigatorSizeRatio:   0.15,
            animationTime:        0.3,
            maxZoomPixelRatio:    2,
            visibilityRatio:      0.5,
            constrainDuringPan:   true,
            gestureSettingsMouse: { clickToZoom: false }
        });

        // Mouse-move → HUD
        new OpenSeadragon.MouseTracker({
            element: viewer.element,
            moveHandler: function (evt) {
                updateHUD(evt.position);
            }
        });

        // Ctrl+Click → fixation target
        viewer.addHandler("canvas-click", function (evt) {
            if (evt.originalEvent.ctrlKey) {
                evt.preventDefaultAction = true;
                var vp  = viewer.viewport.pointFromPixel(evt.position);
                var img = viewer.viewport.viewportToImageCoordinates(vp);
                sendClick(img.x, img.y);
            }
        });

        // Viewport changes → broadcast
        viewer.addHandler("animation",        broadcastViewport);
        viewer.addHandler("animation-finish", broadcastViewport);
        viewer.addHandler("open", function () {
            setTimeout(broadcastViewport, 400);
        });
    }

    // ---------------------------------------------------------------
    // HUD
    // ---------------------------------------------------------------

    function updateHUD(mousePos) {
        if (!viewer || !viewer.viewport || !mousePos) return;

        try {
            var vp  = viewer.viewport.pointFromPixel(mousePos);
            var img = viewer.viewport.viewportToImageCoordinates(vp);

            var bounds = viewer.viewport.getBounds(true);
            var tl = viewer.viewport.viewportToImageCoordinates(
                         bounds.getTopLeft());
            var br = viewer.viewport.viewportToImageCoordinates(
                         bounds.getBottomRight());

            var visibleW = br.x - tl.x;
            var containerW = viewer.viewport.getContainerSize().x;
            var imgPerScreen = visibleW / containerW;

            var mag = null;
            if (slideInfo.objective_power) {
                mag = slideInfo.objective_power / imgPerScreen;
            }

            var maxLvl = slideInfo.dz_max_level;
            var dzLvl  = Math.round(
                maxLvl - Math.log2(Math.max(1, imgPerScreen))
            );
            dzLvl = Math.max(0, Math.min(maxLvl, dzLvl));

            document.getElementById("hud-pos").textContent =
                "Cursor WSI: (" +
                Math.round(img.x) + ", " + Math.round(img.y) + ")";

            document.getElementById("hud-mag").textContent =
                "Magnification: " +
                (mag !== null ? mag.toFixed(2) + "\u00d7" : "N/A");

            document.getElementById("hud-level").textContent =
                "DZ Level: " + dzLvl + " / " + maxLvl;

            document.getElementById("hud-vp").textContent =
                "Viewport: (" +
                Math.round(tl.x) + ", " + Math.round(tl.y) + ") \u2192 (" +
                Math.round(br.x) + ", " + Math.round(br.y) + ")";
        } catch (e) {
            // viewport not ready
        }
    }

    // ---------------------------------------------------------------
    // Keyboard shortcuts
    // ---------------------------------------------------------------

    function initKeyboard() {
        document.addEventListener("keydown", function (evt) {
            // G — cycle gaze render mode
            if (evt.key === "g" || evt.key === "G") {
                if (gazeMode === "all") {
                    gazeMode = "latest";
                } else if (gazeMode === "latest") {
                    gazeMode = "off";
                } else {
                    gazeMode = "all";
                }
                document.getElementById("hud-gaze-mode").textContent =
                    "Gaze: " + gazeMode + "  [G to toggle]";
                console.log("[viewer] Gaze mode: " + gazeMode);
            }

            // C — clear all gaze dots
            if (evt.key === "c" || evt.key === "C") {
                gazePoints = [];
                console.log("[viewer] Gaze cleared");
            }
        });
    }

    // ---------------------------------------------------------------
    // WebSocket
    // ---------------------------------------------------------------

    function initWebSocket() {
        var proto = (location.protocol === "https:") ? "wss:" : "ws:";
        var url   = proto + "//" + location.host + "/ws";

        ws = new WebSocket(url);

        ws.onopen = function () {
            console.log("[ws] connected");
            var el = document.getElementById("hud-ws");
            el.textContent = "WS: connected";
            el.className   = "ws-on";
        };

        ws.onmessage = function (evt) {
            var msg = JSON.parse(evt.data);
            handleIncoming(msg);
        };

        ws.onclose = function () {
            console.log("[ws] disconnected — reconnecting in 2s");
            var el = document.getElementById("hud-ws");
            el.textContent = "WS: disconnected";
            el.className   = "ws-off";
            setTimeout(initWebSocket, 2000);
        };

        ws.onerror = function () {};
    }

    function wsSend(obj) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(obj));
        }
    }

    function sendClick(wsiX, wsiY) {
        var msg = {
            type:      "click",
            wsi_x:     wsiX,
            wsi_y:     wsiY,
            timestamp: Date.now()
        };
        wsSend(msg);
        console.log("[click] (" +
            Math.round(wsiX) + ", " + Math.round(wsiY) + ")");
    }

    var _vpThrottle = 0;
    function broadcastViewport() {
        var now = Date.now();
        if (now - _vpThrottle < 100) return;
        _vpThrottle = now;

        if (!viewer || !viewer.viewport) return;

        var bounds = viewer.viewport.getBounds(true);
        var tl = viewer.viewport.viewportToImageCoordinates(
                     bounds.getTopLeft());
        var br = viewer.viewport.viewportToImageCoordinates(
                     bounds.getBottomRight());
        var containerSize = viewer.viewport.getContainerSize();

        wsSend({
            type: "viewport_update",
            zoom: viewer.viewport.getZoom(true),
            container_width:  Math.round(containerSize.x),
            container_height: Math.round(containerSize.y),
            bounds_wsi: {
                x_min: Math.round(tl.x),
                y_min: Math.round(tl.y),
                x_max: Math.round(br.x),
                y_max: Math.round(br.y)
            }
        });
    }

    // ---------------------------------------------------------------
    // Incoming messages
    // ---------------------------------------------------------------

    function handleIncoming(msg) {
        if (msg.type === "gaze_point") {
            gazePoints.push({
                wsi_x:       msg.wsi_x,
                wsi_y:       msg.wsi_y,
                is_saccade:  msg.is_saccade  || false,
                fixation_id: msg.fixation_id || 0,
                time:        Date.now()
            });
            while (gazePoints.length > MAX_GAZE) {
                gazePoints.shift();
            }
        } else if (msg.type === "gaze_clear") {
            gazePoints = [];
        }
    }

    // ---------------------------------------------------------------
    // Gaze canvas rendering
    // ---------------------------------------------------------------

    function initGazeCanvas() {
        gazeCanvas = document.getElementById("gaze-canvas");
        gazeCtx    = gazeCanvas.getContext("2d");
        sizeCanvas();
        window.addEventListener("resize", sizeCanvas);
    }

    function sizeCanvas() {
        gazeCanvas.width  = gazeCanvas.offsetWidth;
        gazeCanvas.height = gazeCanvas.offsetHeight;
    }

    function renderLoop() {
        renderGaze();
        requestAnimationFrame(renderLoop);
    }

    function renderGaze() {
        if (!gazeCtx || !viewer || !viewer.viewport) return;

        gazeCtx.clearRect(0, 0, gazeCanvas.width, gazeCanvas.height);

        if (gazeMode === "off") return;
        if (gazePoints.length === 0) return;

        var now = Date.now();
        var i, pt, age, alpha, vp, sp, radius;

        if (gazeMode === "latest") {
            // Show only the single most recent non-saccade point
            for (i = gazePoints.length - 1; i >= 0; i--) {
                pt = gazePoints[i];
                if (pt.is_saccade) continue;

                age = now - pt.time;
                if (age > FADE_MS) break;

                alpha = 1.0 - (age / FADE_MS);

                vp = viewer.viewport.imageToViewportCoordinates(
                         new OpenSeadragon.Point(pt.wsi_x, pt.wsi_y));
                sp = viewer.viewport.viewportToViewerElementCoordinates(vp);

                // Larger dot + crosshair for single-point mode
                gazeCtx.beginPath();
                gazeCtx.arc(sp.x, sp.y, 10, 0, 2 * Math.PI);
                gazeCtx.fillStyle =
                    "rgba(255,50,50," + (alpha * 0.7).toFixed(2) + ")";
                gazeCtx.fill();

                // Crosshair
                gazeCtx.strokeStyle =
                    "rgba(255,255,255," + (alpha * 0.5).toFixed(2) + ")";
                gazeCtx.lineWidth = 1;
                gazeCtx.beginPath();
                gazeCtx.moveTo(sp.x - 16, sp.y);
                gazeCtx.lineTo(sp.x + 16, sp.y);
                gazeCtx.moveTo(sp.x, sp.y - 16);
                gazeCtx.lineTo(sp.x, sp.y + 16);
                gazeCtx.stroke();

                break;  // only the latest one
            }
            return;
        }

        // Mode: "all" — show all dots with fade
        for (i = 0; i < gazePoints.length; i++) {
            pt  = gazePoints[i];
            age = now - pt.time;
            if (age > FADE_MS) continue;

            alpha = 1.0 - (age / FADE_MS);

            vp = viewer.viewport.imageToViewportCoordinates(
                     new OpenSeadragon.Point(pt.wsi_x, pt.wsi_y));
            sp = viewer.viewport.viewportToViewerElementCoordinates(vp);

            radius = pt.is_saccade ? 2 : 5;

            gazeCtx.beginPath();
            gazeCtx.arc(sp.x, sp.y, radius, 0, 2 * Math.PI);

            if (pt.is_saccade) {
                gazeCtx.fillStyle =
                    "rgba(150,150,150," + (alpha * 0.3).toFixed(2) + ")";
            } else {
                gazeCtx.fillStyle =
                    "rgba(255,50,50," + (alpha * 0.6).toFixed(2) + ")";
            }
            gazeCtx.fill();
        }
    }

    // ---------------------------------------------------------------
    // Start
    // ---------------------------------------------------------------

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }

})();