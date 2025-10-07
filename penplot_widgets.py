# --- keep these imports as you have them ---
import time, math, threading
from types import SimpleNamespace
from statistics import mean
import ipywidgets as W
from ipycanvas import MultiCanvas, hold_canvas
from IPython.display import display, clear_output
from queue import Queue, Empty

from pattern import Pattern, Polyline, Renderer


# ---------- 1) lift MotionWorker to top level (unchanged logic) ----------
class MotionWorker:
    def __init__(self, grbl, status_cb, hz=40.0, settle=0.0):
        self.grbl = grbl
        self.status_cb = status_cb
        self.interval = 1.0 / float(hz)
        self.settle   = float(settle)
        self.latest_xy = None
        self.cmd_q = Queue()
        self.ev = threading.Event()
        self.lock = threading.Lock()
        self.running = True
        self.script_busy = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _wait_idle(self, timeout=30.0):
        try:
            self.grbl.wait_idle(timeout=timeout)
            if self.settle > 0: time.sleep(self.settle)
        except Exception:
            time.sleep(0.15 + self.settle)

    def _goto(self, x, y):
        self.grbl.pen_up(step=0.10)
        self.grbl.move_xy(float(x), float(y))
        self._wait_idle()
    def _jog(self, x, y):
        self.grbl.move_xy(float(x), float(y))
        self._wait_idle()
    def _set_h(self, h):
        self.grbl.pen_set(float(h), step=0.10)
        if self.settle > 0: time.sleep(self.settle)
    def _sleep(self, s):
        time.sleep(float(s))

    def _run(self):
        last_sent = 0.0
        while self.running:
            handled = False
            try:
                cmd = self.cmd_q.get_nowait()
                handled = True
                kind = cmd[0]
                try:
                    if kind == 'goto':
                        _, x, y = cmd; self._goto(x, y)
                    elif kind == 'jog':
                        _, x, y = cmd; self._jog(x, y)
                    elif kind == 'set_h':
                        _, h = cmd; self._set_h(h)
                    elif kind == 'goto_and_h':
                        _, x, y, h = cmd; self._goto(x, y); self._set_h(h)
                    elif kind == 'sleep':
                        _, s = cmd; self._sleep(s)
                    elif kind == 'barrier':
                        _, ev, clear_busy = cmd
                        if clear_busy: self.script_busy = False
                        ev.set()
                except Exception as e:
                    self.status_cb(f"Device error: {e}\nQueued motion paused. Check port access.")
            except Empty:
                pass

            if not handled and not self.script_busy:
                tgt = None
                with self.lock:
                    tgt = self.latest_xy
                    self.latest_xy = None
                if tgt is not None:
                    dt = time.monotonic() - last_sent
                    if dt < self.interval:
                        time.sleep(self.interval - dt)
                    try:
                        self._goto(*tgt)
                        last_sent = time.monotonic()
                    except Exception as e:
                        self.status_cb(f"Device error: {e}\nLive motion paused. Check port access.")

            if not handled and (self.script_busy or self.latest_xy is None) and self.cmd_q.empty():
                self.ev.wait(timeout=0.05); self.ev.clear()

    # API passthrough (same names)
    def queue_live_xy(self, x, y):
        if self.script_busy:
            return
        with self.lock:
            self.latest_xy = (float(x), float(y))
        self.ev.set()
    def queue_goto(self, x, y):
        self.cmd_q.put(('goto', float(x), float(y))); self.ev.set()
    def queue_jog(self, x, y):
        self.cmd_q.put(('jog', float(x), float(y))); self.ev.set()
    def queue_set_h(self, h):
        self.cmd_q.put(('set_h', float(h))); self.ev.set()
    def queue_goto_and_h(self, x, y, h):
        self.cmd_q.put(('goto_and_h', float(x), float(y), float(h))); self.ev.set()
    def queue_sleep(self, secs):
        self.cmd_q.put(('sleep', float(secs))); self.ev.set()
    def run_script(self, items, wait=False):
        self.script_busy = True
        ev = threading.Event()
        for it in items: self.cmd_q.put(it)
        self.cmd_q.put(('barrier', ev, True)); self.ev.set()
        if wait: ev.wait()
    def stop(self):
        self.running = False; self.ev.set()


class UIRenderer(Renderer):
    def __init__(self, *args, **kwargs):
        self._progress_cb = kwargs.pop("progress_cb", None)
        self._stop_flag   = kwargs.pop("stop_flag", None)
        self._pause_flag  = kwargs.pop("pause_flag", None)
        super().__init__(*args, **kwargs)

    def _run_polyline(self, it: Polyline):
        if len(it.pts) < 2:
            return
        pts = list(reversed(it.pts)) if it._rev else list(it.pts)
        x0, y0 = pts[0]
        self._travel_to(x0, y0)

        # honor per-polyline draw feed immediately
        if it.feed_draw is not None and hasattr(self.g, "cfg"):
            try:
                self.g.cfg.feed_draw = int(it.feed_draw)
            except Exception:
                pass

        mode = self.z_mode; thr = self.z_threshold

        def maybe_stop_pause():
            if self._stop_flag and self._stop_flag.is_set():
                raise RuntimeError("Stopped")
            if self._pause_flag and self._pause_flag.is_set():
                while self._pause_flag.is_set():
                    time.sleep(0.05)

        if mode in ("start", "centroid"):
            if mode == "start":
                zx, zy = pts[0]
            else:
                zx = sum(p[0] for p in pts) / len(pts)
                zy = sum(p[1] for p in pts) / len(pts)
            z = self._pen_pos(zx, zy, -0.1); self._pen_set(z, settle=True)
            for i in range(1, len(pts)):
                maybe_stop_pause()
                ex, ey = pts[i]; self.g.draw_xy(ex, ey, wait=False)
                if self._progress_cb: self._progress_cb(1)
                if i % self.flush_every == 0: self.g.wait_idle()
        elif mode == "per_segment":
            sx, sy = pts[0]; ex, ey = pts[1]
            mx, my = 0.5*(sx+ex), 0.5*(sy+ey)
            z = self._pen_pos(mx, my, -0.1); self._pen_set(z, settle=True)
            for i in range(1, len(pts)):
                maybe_stop_pause()
                sx, sy = pts[i-1]; ex, ey = pts[i]
                mx, my = 0.5*(sx+ex), 0.5*(sy+ey)
                z = self._pen_pos(mx, my, -0.1); self._pen_set(z, settle=False)
                self.g.draw_xy(ex, ey, wait=False)
                if self._progress_cb: self._progress_cb(1)
                if i % self.flush_every == 0: self.g.wait_idle()
        elif mode == "threshold":
            sx, sy = pts[0]; ex, ey = pts[1]
            mx, my = 0.5*(sx+ex), 0.5*(sy+ey)
            cur_z = self._pen_pos(mx, my, -0.1); self._pen_set(cur_z, settle=True)
            for i in range(1, len(pts)):
                maybe_stop_pause()
                sx, sy = pts[i-1]; ex, ey = pts[i]
                mx, my = 0.5*(sx+ex), 0.5*(sy+ey)
                z = self._pen_pos(mx, my, -0.1)
                if abs(z - cur_z) > thr:
                    self._pen_set(z, settle=False); cur_z = z
                self.g.draw_xy(ex, ey, wait=False)
                if self._progress_cb: self._progress_cb(1)
                if i % self.flush_every == 0: self.g.wait_idle()
        else:
            raise ValueError(f"Unknown z_mode: {mode}")

        self.g.wait_idle(); self._partial_lift_to_travel(pts[-1]); self._cur_xy = pts[-1]

def ingest_pattern(pat, renderer_cfg=None):
    # keep the actual Pattern so Start can run *exactly* what you sent
    ctx._LAST_PATTERN = pat
    ctx._RENDERER_CFG = dict(renderer_cfg or {})

    # draw overlay from the pattern (preserve pen + feed_draw for later)
    strokes = []
    for it in getattr(pat, "items", []):
        if isinstance(it, Polyline) and len(it.pts) >= 2:
            strokes.append({
                "pts": it.pts,
                "color": "#111",
                "width": 1.5,
                "pen": f"pen{getattr(it, 'pen_id', 0)}",
                "feed_draw": getattr(it, "feed_draw", None),
            })
    draw_paths(strokes, cache=True)
    ctx._update_pens_from_strokes()
    ctx.status("Pattern ingested.")



def _wire_handlers(ctx):
    # ---------- slider ----------
    def on_height_change(change):
        if getattr(ctx, "_suppress_h_event", False):
            return
        kind = ctx.selected[0]
        if kind == 'corner':
            key = ctx.selected[1]
            ctx.corner[key][2] = float(ctx.h_slider.value)
            ctx.motion.queue_set_h(ctx.corner[key][2])
        elif kind == 'pot':
            pid = ctx.selected[1]
            pot = next((p for p in ctx.pots if p['id'] == pid), None)
            if pot:
                pot['h'] = float(ctx.h_slider.value)
                ctx.motion.queue_set_h(pot['h'])
                _refresh_pot_list(ctx, selected_id=pid)
        ctx.request_redraw()
    ctx.h_slider.observe(on_height_change, names='value')

    # ---------- pots ----------
    def on_add_pot(_):
        x0, y0, x1, y1 = _rect_bounds(ctx.corner)
        cx = x0 + 0.20 * (x1 - x0)
        cy = (y0 + y1) / 2.0
        new = {'id': ctx._next_pot_id, 'x': cx, 'y': cy, 'h': 1.0, 'color': ctx.pot_color.value}
        ctx.pots.append(new); ctx._next_pot_id += 1
        _refresh_pot_list(ctx, selected_id=new['id'])
        _set_selected_impl(ctx, ('pot', new['id']))
        ctx.request_redraw()

    def on_delete_pot(_):
        if ctx.selected[0] == 'pot':
            pid = ctx.selected[1]
            idx = next((i for i, p in enumerate(ctx.pots) if p['id'] == pid), None)
            if idx is not None:
                ctx.pots.pop(idx)
                _safe_unobserve(ctx.pot_color, getattr(ctx, "_pot_color_handler", None), names='value')
                ctx._pot_color_handler = None
                ctx.pot_color.disabled = True
                _refresh_pot_list(ctx, selected_id=None)
                _set_selected_impl(ctx, ('corner', 'BL'), move_and_set=False)
                ctx.request_redraw()


    def on_pot_select(change):
        if getattr(ctx, "_suppress_list_event", False):
            return
        if change['name'] == 'value' and change['new'] is not None:
            _set_selected_impl(ctx, ('pot', change['new']))

    def on_pot_color_change(change):
        if ctx.selected[0] == 'pot':
            pid = ctx.selected[1]
            pot = next((p for p in ctx.pots if p['id'] == pid), None)
            if pot:
                pot['color'] = ctx.pot_color.value
                ctx.request_redraw()
                _refresh_pot_list(ctx, selected_id=pid)

    ctx.add_pot_btn.on_click(on_add_pot)
    ctx.del_pot_btn.on_click(on_delete_pot)
    ctx.pot_list.observe(on_pot_select, names='value')
    ctx.pot_color.observe(on_pot_color_change, names='value')
    ctx.pot_color.disabled = True

    # ---------- canvas interactions ----------
    ctx.dragging = False
    ctx.drag_kind = None
    ctx.drag_key  = None
    ctx.pick_radius_px = 14 * ctx.DPR

    def hit_test_jog(cx, cy):
        for jb in ctx.jog_boxes:
            x, y, w, h = jb['rect']
            if (x <= cx <= x+w) and (y <= cy <= y+h):
                return jb
        return None

    def hit_test_entity(cx, cy):
        if not ctx.show_overlay:
            return None
        ccx, ccy, _ = _center_info(ctx.corner, ctx.CORNERS)
        ccxp, ccyp = ctx.w2c(ccx, ccy)
        if math.hypot(cx - ccxp, cy - ccyp) <= ctx.pick_radius_px:
            return ('center', None)
        for c in ctx.CORNERS:
            px, py = ctx.w2c(ctx.corner[c][0], ctx.corner[c][1])
            if math.hypot(cx - px, cy - py) <= ctx.pick_radius_px:
                return ('corner', c)
        for p in ctx.pots:
            px, py = ctx.w2c(p['x'], p['y'])
            if math.hypot(cx - px, cy - py) <= ctx.pick_radius_px:
                return ('pot', p['id'])
        return None

    def apply_jog_corner(c_key, dx, dy):
        if not ctx.show_overlay or c_key not in ctx.DRAGGABLE:
            return
        if c_key == 'BL':
            nx = max(0.0, min(ctx.corner['BL'][0] + dx, ctx.corner['TR'][0]))
            ny = max(0.0, min(ctx.corner['BL'][1] + dy, ctx.corner['TR'][1]))
            ctx.corner['BL'][0], ctx.corner['BL'][1] = nx, ny
        elif c_key == 'TR':
            nx = max(ctx.corner['BL'][0], min(ctx.corner['TR'][0] + dx, ctx.x_max))
            ny = max(ctx.corner['BL'][1], min(ctx.corner['TR'][1] + dy, ctx.y_max))
            ctx.corner['TR'][0], ctx.corner['TR'][1] = nx, ny
        _enforce_rect_from_BL_TR(ctx.corner, ctx.x_max, ctx.y_max)
        ctx.request_redraw()
        ctx.motion.queue_jog(ctx.corner[c_key][0], ctx.corner[c_key][1])

    def apply_jog_pot(pid, dx, dy):
        if not ctx.show_overlay:
            return
        pot = next((p for p in ctx.pots if p['id'] == pid), None)
        if not pot:
            return
        nx = max(0.0, min(pot['x'] + dx, ctx.x_max))
        ny = max(0.0, min(pot['y'] + dy, ctx.y_max))
        pot['x'], pot['y'] = nx, ny
        ctx.request_redraw()
        ctx.motion.queue_jog(nx, ny)

    def on_mouse_down(x, y):
        if not ctx.show_overlay:
            return

        # jog buttons act immediately, and do not arm dragging
        jb = hit_test_jog(x, y)
        if jb is not None:
            if jb['kind'] == 'corner':
                _set_selected_impl(ctx, ('corner', jb['key']), move_and_set=False)
                apply_jog_corner(jb['key'], jb['dx'], jb['dy'])
            else:
                _set_selected_impl(ctx, ('pot', jb['id']), move_and_set=False)
                apply_jog_pot(jb['id'], jb['dx'], jb['dy'])
            ctx._drag_arm = None
            return

        hit = hit_test_entity(x, y)
        if hit is None:
            ctx._drag_arm = None
            return

        draggable = (hit[0] == 'corner' and hit[1] in ctx.DRAGGABLE) or (hit[0] == 'pot')

        # second click on the same draggable target -> start dragging
        if draggable and ctx._drag_arm == hit:
            ctx.dragging, ctx.drag_kind, ctx.drag_key = True, hit[0], hit[1]
            ctx._drag_arm = None
            return

        # first click: select and move there, do not drag yet
        _set_selected_impl(ctx, hit, move_and_set=True)

        # arm for drag on next click if draggable
        ctx._drag_arm = hit if draggable else None
        ctx.dragging, ctx.drag_kind, ctx.drag_key = False, None, None


    def on_mouse_move(x, y):
        if not ctx.show_overlay or not ctx.dragging:
            return
        wx, wy = ctx.c2w(x, y)
        if ctx.drag_kind == 'corner':
            if ctx.drag_key == 'BL':
                wx = max(0.0, min(wx, ctx.corner['TR'][0]))
                wy = max(0.0, min(wy, ctx.corner['TR'][1]))
                ctx.corner['BL'][0], ctx.corner['BL'][1] = wx, wy
            elif ctx.drag_key == 'TR':
                wx = max(ctx.corner['BL'][0], min(wx, ctx.x_max))
                wy = max(ctx.corner['BL'][1], min(wy, ctx.y_max))
                ctx.corner['TR'][0], ctx.corner['TR'][1] = wx, wy
            _enforce_rect_from_BL_TR(ctx.corner, ctx.x_max, ctx.y_max)
            ctx.request_redraw()
            ctx.motion.queue_live_xy(wx, wy)
        elif ctx.drag_kind == 'pot':
            pot = next((p for p in ctx.pots if p['id'] == ctx.drag_key), None)
            if pot:
                pot['x'], pot['y'] = wx, wy
                ctx.request_redraw()
                ctx.motion.queue_live_xy(wx, wy)

    def on_mouse_up(x, y):
        ctx.dragging = False
        ctx.drag_kind = None
        ctx.drag_key  = None

    ctx.layers.on_mouse_down(on_mouse_down)
    ctx.layers.on_mouse_move(on_mouse_move)
    ctx.layers.on_mouse_up(on_mouse_up)

    # ---------- actions ----------
    def _save_state_apply():
        state = {
            'corners': {k: {'x': ctx.corner[k][0], 'y': ctx.corner[k][1], 'h': ctx.corner[k][2]} for k in ctx.CORNERS},
            'pots':    [{'id': p['id'], 'x': p['x'], 'y': p['y'], 'h': p['h'], 'color': p['color']} for p in ctx.pots],
        }
        ctx._PPW_STATE[ctx._state_key] = state
        try:
            ctx.grbl.set_compensation_from_widget(state)
        except Exception as e:
            ctx.status(f"Apply compensation failed: {e}")

    def on_sweep_rect(_):
        x0, y0, x1, y1 = _rect_bounds(ctx.corner)
        w = abs(x1 - x0); h = abs(y1 - y0)
        try:
            ctx.grbl.pen_up(step=0.10)
            ctx.grbl.sweep_rect(w, h, min(x0, x1), min(y0, y1))
            ctx.status(f"Swept rectangle: ({x0:.2f},{y0:.2f}) -> ({x1:.2f},{y1:.2f})")
        except Exception as e:
            ctx.status(f"Sweep error: {e}")


    def on_pen_up(_):   ctx.motion.queue_set_h(1.0)
    def on_pen_down(_): ctx.motion.queue_set_h(0.0)
    def on_home(_):     ctx.motion.queue_goto(0.0, 0.0)
    def on_save_apply(_):
        _save_state_apply()
        ctx.status("Saved and applied compensation.")

    def on_reset_heights(_):
        for k in ctx.CORNERS: ctx.corner[k][2] = 1.0
        for p in ctx.pots:    p['h'] = 1.0
        if ctx.selected[0] == 'corner':
            ctx.h_slider.value = ctx.corner[ctx.selected[1]][2]
        elif ctx.selected[0] == 'pot':
            pid = ctx.selected[1]
            pot = next((pp for pp in ctx.pots if pp['id'] == pid), None)
            if pot: ctx.h_slider.value = pot['h']
        else:
            _, _, hc = _center_info(ctx.corner, ctx.CORNERS)
            ctx.h_slider.value = hc
        ctx.motion.queue_set_h(1.0)
        _refresh_pot_list(ctx)
        ctx.request_redraw()
        ctx.status("All pen heights reset to 1.00.")

    def on_toggle_overlay(_):
        ctx.show_overlay = not ctx.show_overlay
        ctx.overlay_btn.description = "Show Overlay" if not ctx.show_overlay else "Hide Overlay"
        ctx.request_redraw()
        ctx.status("Overlay hidden." if not ctx.show_overlay else "Overlay shown.")

    def _set_rectangle_size(width_mm, height_mm):
        ctx.corner['BL'][0], ctx.corner['BL'][1] = 0.0, 0.0
        ctx.corner['TR'][0], ctx.corner['TR'][1] = min(ctx.x_max, float(width_mm)), min(ctx.y_max, float(height_mm))
        _enforce_rect_from_BL_TR(ctx.corner, ctx.x_max, ctx.y_max)
        _set_selected_impl(ctx, ('corner', 'BL'), move_and_set=False)
        ctx.request_redraw()
        ctx.status(f"Rectangle set to {ctx.corner['TR'][0]:.1f} x {ctx.corner['TR'][1]:.1f} mm (from BL).")
    def on_set_a4(_):  _set_rectangle_size(297.0, 210.0)
    def on_set_a5(_):  _set_rectangle_size(210.0, 148.5)
    def on_set_15(_):  _set_rectangle_size(150.0, 150.0)
    def on_set_10(_):  _set_rectangle_size(100.0, 100.0)


    # attach
    ctx.sweep_btn.on_click(on_sweep_rect)
    ctx.penup_btn.on_click(on_pen_up)
    ctx.pendown_btn.on_click(on_pen_down)
    ctx.home_btn.on_click(on_home)
    ctx.save_btn.on_click(on_save_apply)
    ctx.delplot_btn.on_click(lambda _: (ctx._PPW_API["clear_plot"](), ctx.status("Plot overlay deleted.")))
    ctx.resetZ_btn.on_click(on_reset_heights)
    ctx.overlay_btn.on_click(on_toggle_overlay)

    ctx.a4_btn.on_click(on_set_a4)
    ctx.a5_btn.on_click(on_set_a5)
    ctx.s15_btn.on_click(on_set_15)
    ctx.s10_btn.on_click(on_set_10)



    ctx._stop_flag = threading.Event()
    ctx._pause_flag = threading.Event()

    def _on_progress(n_inc):
        ctx._done_steps = getattr(ctx, "_done_steps", 0) + int(n_inc)
        tot = max(1, getattr(ctx, "_total_steps", 1))
        if ctx._done_steps > tot: ctx._done_steps = tot
        ctx.prog.value = ctx._done_steps / float(tot)
        pct = int(round(ctx.prog.value * 100))
        ctx.prog_label.value = f"{pct}%   {ctx._done_steps} / {tot} segments"

    ctx._on_progress = _on_progress

    def _pattern_from_strokes(strokes, pen_filter_set=None):
        p = Pattern()
        for s in strokes:
            pts = list(s["pts"])
            if len(pts) < 2:
                continue

            pen_str = s.get("pen", "pen0")
            try: pid = int(''.join(ch for ch in pen_str if ch.isdigit()))
            except Exception: pid = 0
            if pen_filter_set is not None and pid not in pen_filter_set:
                continue

            # accept typical keys for draw feed
            feed_val = s.get("feed_draw") or s.get("feed") or s.get("feedrate") or s.get("speed")
            try: feed_val = int(feed_val) if feed_val is not None else None
            except Exception: feed_val = None

            if feed_val is not None:
                p.add(Polyline(pts=pts, pen_id=pid, feed_draw=feed_val))
            else:
                p.add(Polyline(pts=pts, pen_id=pid))
        return p


    def _preview_clicked(_):
        if ctx._LAST_PATTERN is None and not ctx._LAST_STROKES:
            ctx.status("No data. Use r.plot(p) or send paths.")
            return

        # prefer the ingested Pattern
        if ctx._LAST_PATTERN is not None:
            p = ctx._LAST_PATTERN
            cfg = ctx._RENDERER_CFG or {}
            r = Renderer(ctx.grbl, **cfg)   # use same settings you sent
        else:
            # fallback: build from strokes, use UI settings
            p = _pattern_from_strokes(ctx._LAST_STROKES, set(ctx.pens_multi.value) if ctx.pens_multi.value else None)
            r = Renderer(ctx.grbl,
                        z_mode=ctx.z_mode.value, z_threshold=float(ctx.z_threshold.value),
                        settle_down_s=float(ctx.settle_down.value), settle_up_s=float(ctx.settle_up.value),
                        z_step=float(ctx.z_step.value) if ctx.z_step.value > 0 else None,
                        z_step_delay=float(ctx.z_step_delay.value),
                        flush_every=int(ctx.flush_every.value), feed_travel=int(ctx.feed_travel.value),
                        lift_delta=float(ctx.lift_delta.value))
        r.attach_widget_api(ctx._PPW_API)
        ctx._in_preview = True
        try:
            r.plot(p)
            ctx.status("Preview sent.")
        finally:
            ctx._in_preview = False
            ctx._plot_draw_paths(ctx._LAST_STROKES, cache=True)



    def _run_clicked(_):
        if not getattr(ctx, "_LAST_PATTERN", None):
            ctx.status("No pattern in widget. Send a Pattern first.")
            return
        if getattr(ctx, "_run_thread", None) and ctx._run_thread.is_alive():
            ctx.status("Run already in progress.")
            return

        p = ctx._LAST_PATTERN
        cfg = getattr(ctx, "_RENDERER_CFG", {})

        # respect pen filter
        pen_set = set(ctx.pens_multi.value) if ctx.pens_multi.value else None

        # seed progress bar
        segs = sum(max(0, len(it.pts) - 1) for it in getattr(p, "items", []) if isinstance(it, Polyline))
        ctx._total_steps = max(1, segs); ctx._done_steps = 0
        ctx.prog.value = 0.0; ctx.prog_label.value = f"0% of {ctx._total_steps} segments"
        ctx._stop_flag.clear(); ctx._pause_flag.clear(); ctx.pause_btn.value = False

        # build the real Renderer using *the same* settings you sent
        r = Renderer(ctx.grbl, **{
            "z_mode": cfg.get("z_mode", ctx.z_mode.value),
            "z_threshold": cfg.get("z_threshold", float(ctx.z_threshold.value)),
            "settle_down_s": cfg.get("settle_down_s", float(ctx.settle_down.value)),
            "settle_up_s": cfg.get("settle_up_s", float(ctx.settle_up.value)),
            "z_step": cfg.get("z_step", (float(ctx.z_step.value) if ctx.z_step.value > 0 else None)),
            "z_step_delay": cfg.get("z_step_delay", float(ctx.z_step_delay.value)),
            "flush_every": cfg.get("flush_every", int(ctx.flush_every.value)),
            "feed_travel": cfg.get("feed_travel", int(ctx.feed_travel.value)),
            "lift_delta": cfg.get("lift_delta", float(ctx.lift_delta.value)),
        })
        r.attach_widget_api(ctx._PPW_API)

        start_xy = (float(ctx.start_x.value), float(ctx.start_y.value))
        opt = 'nn' if ctx.optimize_nn.value else None
        combine_cfg = {'join_tol_mm': float(ctx.combine_tol.value)} if ctx.combine_on.value else None
        rs_max_seg = float(ctx.resample_seg.value) if ctx.resample_seg.value.strip() else None
        resample_cfg = {'max_dev_mm': float(ctx.resample_dev.value), 'max_seg_mm': rs_max_seg} if ctx.resample_on.value else None

        # (optional) prime first feed rate if firmware latches it
        try:
            first = next((it for it in getattr(p, "items", [])
                        if isinstance(it, Polyline) and getattr(it, "feed_draw", None) is not None), None)
            if first is not None and hasattr(ctx.grbl, "cfg"):
                ctx.grbl.cfg.feed_draw = int(first.feed_draw)
        except Exception:
            pass

        def _runner():
            t0 = time.time(); ctx._in_preview = True
            try:
                r.run(p,
                    pen_filter=(None if pen_set is None else list(pen_set)),
                    start_xy=start_xy,
                    optimize=opt,
                    combine=combine_cfg,
                    resample=resample_cfg,
                    return_home=True,
                    preview_in_widget=True)
                ctx.status("Run finished.")
            except RuntimeError as e:
                ctx.status(f"Run stopped: {e}")
            except Exception as e:
                ctx.status(f"Run error: {e}")
            finally:
                ctx._in_preview = False
                ctx._plot_draw_paths(ctx._LAST_STROKES, cache=True)
                with ctx.log_out: print(f"Done in {time.time()-t0:.1f}s")
                ctx.pause_btn.value = False; ctx._pause_flag.clear()

        ctx._run_thread = threading.Thread(target=_runner, daemon=True)
        ctx._run_thread.start()
        ctx.status("Run started.")


    def _pause_toggled(change):
        if change['name'] == 'value':
            if change['new']:
                ctx._pause_flag.set()
                ctx.pause_btn.description = "Resume"
                ctx.status("Paused.")
            else:
                ctx._pause_flag.clear()
                ctx.pause_btn.description = "Pause"
                ctx.status("Resumed.")

    def _stop_clicked(_):
        ctx._stop_flag.set()
        ctx.status("Stopping...")

    ctx.preview_btn.on_click(_preview_clicked)
    ctx.run_btn.on_click(_run_clicked)
    ctx.pause_btn.observe(_pause_toggled, names='value')
    ctx.stop_btn.on_click(_stop_clicked)


def _refresh_pot_list(ctx, selected_id=None):
    ctx._suppress_list_event = True
    try:
        opts = [(f"#{p['id']}  ({p['x']:.1f},{p['y']:.1f})  h={p['h']:.2f}  {p['color']}", p['id']) for p in ctx.pots]
        cur = ctx.pot_list.value
        ctx.pot_list.options = opts
        if selected_id is not None:
            ctx.pot_list.value = selected_id
        elif cur in [p['id'] for p in ctx.pots]:
            ctx.pot_list.value = cur
        elif ctx.pots:
            ctx.pot_list.value = ctx.pots[0]['id']
    finally:
        ctx._suppress_list_event = False



# ---------- 2) tiny geometry and persistence helpers ----------
def _enforce_rect_from_BL_TR(corner, x_max, y_max):
    BLx, BLy = corner["BL"][0], corner["BL"][1]
    TRx, TRy = corner["TR"][0], corner["TR"][1]
    BLx = max(0.0, min(BLx, TRx))
    BLy = max(0.0, min(BLy, TRy))
    TRx = max(BLx, min(TRx, x_max))
    TRy = max(BLy, min(TRy, y_max))
    corner["BL"][0], corner["BL"][1] = BLx, BLy
    corner["TR"][0], corner["TR"][1] = TRx, TRy
    corner["TL"][0], corner["TL"][1] = BLx, TRy
    corner["BR"][0], corner["BR"][1] = TRx, BLy

def _rect_bounds(corner):
    BLx, BLy = corner["BL"][0], corner["BL"][1]
    TRx, TRy = corner["TR"][0], corner["TR"][1]
    return BLx, BLy, TRx, TRy

def _center_info(corner, CORNERS):
    x0, y0, x1, y1 = _rect_bounds(corner)
    cx, cy = (x0+x1)/2.0, (y0+y1)/2.0
    hc = mean([corner[k][2] for k in CORNERS])
    return cx, cy, hc

def _collect_state(corner, pots, CORNERS):
    x0, y0, x1, y1 = _rect_bounds(corner)
    rect = {'BL': (x0, y0), 'TR': (x1, y1)}
    corners_out = {k: {'x': corner[k][0], 'y': corner[k][1], 'h': corner[k][2]} for k in CORNERS}
    pots_out = [{'id': p['id'], 'x': p['x'], 'y': p['y'], 'h': p['h'], 'color': p['color']} for p in pots]
    cx, cy, hc = _center_info(corner, CORNERS)
    return {'rectangle': rect, 'corners': corners_out, 'pots': pots_out, 'center': {'x': cx, 'y': cy, 'h': hc}}


# ---------- 3) split builders: canvas, controls, runner, and plot API ----------
def _build_canvas(ctx):
    DPR = 2
    ctx.DPR = DPR
    css_pad_left, css_pad_right, css_pad_top, css_pad_bottom = 36, 28, 26, 42
    css_bed_w  = 560
    css_bed_h  = int(round(css_bed_w * (ctx.y_max / ctx.x_max)))
    css_total_w = css_bed_w + css_pad_left + css_pad_right
    css_total_h = css_bed_h + css_pad_top + css_pad_bottom

    padL_px = css_pad_left * DPR; padR_px = css_pad_right * DPR
    padT_px = css_pad_top * DPR;  padB_px = css_pad_bottom * DPR
    bed_w_px = css_bed_w * DPR;   bed_h_px = css_bed_h * DPR
    total_w_px = bed_w_px + padL_px + padR_px
    total_h_px = bed_h_px + padT_px + padB_px

    layers = MultiCanvas(3, width=total_w_px, height=total_h_px,
                         layout=W.Layout(width=f"{css_total_w}px",
                                         height=f"{css_total_h}px",
                                         border="1px solid #eee",
                                         align_self="flex-start"))
    try: layers.layout.cursor = "crosshair"
    except Exception: pass

    ctx.layers = layers
    ctx.bg, ctx.plot, ctx.fg = layers[0], layers[1], layers[2]
    ctx.padL_px, ctx.padT_px, ctx.bed_w_px, ctx.bed_h_px = padL_px, padT_px, bed_w_px, bed_h_px

    sx = bed_w_px / ctx.x_max
    sy = bed_h_px / ctx.y_max
    def w2c(x, y):  # world to canvas
        return padL_px + x * sx, padT_px + (bed_h_px - y * sy)
    def c2w(cx, cy):  # canvas to world
        x = (cx - padL_px) / sx
        y = (bed_h_px - (cy - padT_px)) / sy
        return max(0.0, min(ctx.x_max, x)), max(0.0, min(ctx.y_max, y))
    ctx.w2c = w2c
    ctx.c2w = c2w
    
def _safe_unobserve(widget, handler, names=None):
    if handler is None:
        return
    try:
        widget.unobserve(handler, names=names)
    except Exception:
        pass

def _build_basic_controls(ctx):
    # reuse exact widgets and labels to avoid breaking anything
    ctx.h_slider = W.FloatSlider(min=0.0, max=1.0, step=0.01, value=ctx.corner['BL'][2],
                                 orientation="vertical", readout_format=".2f",
                                 description="Height", continuous_update=True,
                                 layout=W.Layout(height="100%", min_height="420px", width="70px"))
    ctx._suppress_h_event = False

    ctx.sweep_btn   = W.Button(description="Sweep Rectangle", button_style="info")
    ctx.penup_btn   = W.Button(description="Pen Up (1.0)")
    ctx.pendown_btn = W.Button(description="Pen Down (0.0)")
    ctx.home_btn    = W.Button(description="Home (0,0)")
    ctx.save_btn    = W.Button(description="Save & Apply", button_style="success")
    ctx.delplot_btn = W.Button(description="Delete Plot", button_style="danger")
    ctx.resetZ_btn  = W.Button(description="Reset Pen Heights")
    ctx.overlay_btn = W.Button(description="Hide Overlay", button_style="warning")

    ctx.a4_btn  = W.Button(description="A4")
    ctx.a5_btn  = W.Button(description="A5")
    ctx.s15_btn = W.Button(description="15cm")
    ctx.s10_btn = W.Button(description="10cm")


    ctx.add_pot_btn = W.Button(description="+ Pot")
    ctx.del_pot_btn = W.Button(description="Delete Pot", button_style="danger")
    ctx.pot_color   = W.ColorPicker(description='Pot Color', value="#3a86ff", layout=W.Layout(width="180px"))
    ctx.pot_list    = W.Select(options=[], rows=6, layout=W.Layout(width="260px"))
    ctx._suppress_list_event = False

    ctx.status_out = W.Output(layout=W.Layout(border="1px solid #ddd", padding="6px"))
    ctx.sel_label  = W.HTML("<b>Selected:</b> Corner BL")

def _status_factory(ctx):
    ctx._last_status = None
    ctx._last_status_t = 0.0
    def _status(msg):
        now = time.monotonic()
        if msg == ctx._last_status and (now - ctx._last_status_t) < 0.75:
            return
        ctx._last_status = msg; ctx._last_status_t = now
        with ctx.status_out:
            clear_output(wait=True)
            print(msg)
    ctx.status = _status
    return _status


def _make_renderer(ctx, for_run=False):
    return UIRenderer(
        ctx.grbl,
        z_mode=ctx.z_mode.value or "centroid",
        z_threshold=float(ctx.z_threshold.value),
        settle_down_s=float(ctx.settle_down.value),
        settle_up_s=float(ctx.settle_up.value),
        z_step=float(ctx.z_step.value) if ctx.z_step.value > 0 else None,
        z_step_delay=float(ctx.z_step_delay.value),
        flush_every=int(ctx.flush_every.value),
        feed_travel=int(ctx.feed_travel.value),
        lift_delta=float(ctx.lift_delta.value),
        progress_cb=(ctx._on_progress if for_run else None),
        stop_flag=(ctx._stop_flag if for_run else None),
        pause_flag=(ctx._pause_flag if for_run else None),
    )



def _build_runner_panel(ctx):
    # identical widgets and defaults
    z_mode = W.ToggleButtons(options=[('start','start'), ('centroid','centroid'), ('per_segment','per_segment'), ('threshold','threshold')],
                             value='per_segment', description='z_mode', layout=W.Layout(width="100%"))
    z_threshold = W.BoundedFloatText(value=0.02, min=0.0, max=1e3, step=0.01, description='z_threshold')
    settle_down = W.BoundedFloatText(value=0.05, min=0.0, max=5.0, step=0.01, description='settle_down_s')
    settle_up = W.BoundedFloatText(value=0.03, min=0.0, max=5.0, step=0.01, description='settle_up_s')
    z_step = W.BoundedFloatText(value=0.1, min=0.0, max=1.0, step=0.01, description='z_step')
    z_step_delay = W.BoundedFloatText(value=0.03, min=0.0, max=1.0, step=0.005, description='z_step_delay')
    lift_delta = W.BoundedFloatText(value=0.2, min=0.0, max=1.0, step=0.01, description='lift_delta')
    flush_every = W.IntText(value=200, description='flush_every')
    feed_travel = W.IntText(value=getattr(ctx.grbl.cfg, "feed_travel", 15000) or 15000, description='feed_travel')

    start_x = W.BoundedFloatText(value=0.0, min=0.0, max=ctx.x_max, step=0.1, description='start_x')
    start_y = W.BoundedFloatText(value=0.0, min=0.0, max=ctx.y_max, step=0.1, description='start_y')
    optimize_nn = W.Checkbox(value=False, description='optimize nn')
    combine_on = W.Checkbox(value=True, description='combine endpoints')
    combine_tol = W.BoundedFloatText(value=0.05, min=0.0, max=10.0, step=0.01, description='join_tol')
    resample_on = W.Checkbox(value=True, description='resample')
    resample_dev = W.BoundedFloatText(value=0.1, min=0.0, max=10.0, step=0.01, description='max_dev')
    resample_seg = W.Text(value="", description='max_seg (mm)')

    pens_label = W.HTML("<b>Pen filter</b>")
    pens_multi = W.SelectMultiple(options=[], rows=6, layout=W.Layout(width="160px"))

    preview_btn = W.Button(description="Preview in overlay", button_style="info")
    run_btn = W.Button(description="Start", button_style="success")
    pause_btn = W.ToggleButton(description="Pause", value=False)
    stop_btn = W.Button(description="Stop", button_style="danger")

    prog = W.FloatProgress(value=0.0, min=0.0, max=1.0, bar_style='', layout=W.Layout(width="100%"))
    prog_label = W.HTML("Idle")
    log_out = W.Output(layout=W.Layout(border="1px dashed #ccc", padding="6px", max_height="140px", overflow_y="auto"))

    # store on ctx for handlers
    ctx.z_mode, ctx.z_threshold = z_mode, z_threshold
    ctx.settle_down, ctx.settle_up = settle_down, settle_up
    ctx.z_step, ctx.z_step_delay = z_step, z_step_delay
    ctx.lift_delta, ctx.flush_every, ctx.feed_travel = lift_delta, flush_every, feed_travel
    ctx.start_x, ctx.start_y = start_x, start_y
    ctx.optimize_nn = optimize_nn
    ctx.combine_on, ctx.combine_tol = combine_on, combine_tol
    ctx.resample_on, ctx.resample_dev, ctx.resample_seg = resample_on, resample_dev, resample_seg
    ctx.pens_multi = pens_multi
    ctx.preview_btn, ctx.run_btn, ctx.pause_btn, ctx.stop_btn = preview_btn, run_btn, pause_btn, stop_btn
    ctx.prog, ctx.prog_label, ctx.log_out = prog, prog_label, log_out

    renderer_box = W.VBox([
        W.HTML("<b>Renderer</b>"),
        z_mode,
        W.HBox([z_threshold, lift_delta]),
        W.HBox([settle_down, settle_up]),
        W.HBox([z_step, z_step_delay]),
        W.HBox([flush_every, feed_travel]),
    ], layout=W.Layout(gap="6px"))

    options_box = W.VBox([
        W.HTML("<b>Run options</b>"),
        W.HBox([start_x, start_y, optimize_nn]),
        W.HBox([combine_on, combine_tol]),
        W.HBox([resample_on, resample_dev, resample_seg]),
    ], layout=W.Layout(gap="6px"))

    pens_box = W.VBox([pens_label, pens_multi], layout=W.Layout(gap="6px"))
    controls_row = W.HBox([preview_btn, run_btn, pause_btn, stop_btn], layout=W.Layout(gap="8px"))
    progress_box = W.VBox([W.HTML("<b>Progress</b>"), prog, prog_label, log_out], layout=W.Layout(gap="6px"))

    return W.VBox([renderer_box, options_box, pens_box, controls_row, progress_box],
                  layout=W.Layout(min_width="340px", width="360px", gap="8px"))


def _plot_api_factory(ctx):
    def clear_plot():
        with hold_canvas(ctx.plot):
            ctx.plot.clear()
        ctx._LAST_STROKES.clear()

    def draw_paths(paths, cache=True):
        """
        paths can be:
          1) [ [(x,y),...], ... ]
          2) [ {"pts":[...], "color":"#000", "width":1.5, "pen":"A"}, ... ]
        cache=False means do not overwrite ctx._LAST_STROKES.
        """
        strokes = []
        if not paths:
            clear_plot()
            return

        if isinstance(paths, list) and paths and isinstance(paths[0], dict):
            for s in paths:
                pts = s.get("pts", [])
                if not pts or len(pts) < 2:
                    continue
                strokes.append({
                    "pts": pts,
                    "color": s.get("color", "#111"),
                    "width": float(s.get("width", 1.5)),
                    "pen": s.get("pen", "pen0"),
                })
        else:
            for pts in paths:
                if not pts or len(pts) < 2:
                    continue
                strokes.append({"pts": pts, "color": "#111", "width": 1.5, "pen": "pen0"})

        # Only update cache if requested
        if cache:
            ctx._LAST_STROKES[:] = strokes

        by_pen = {}
        order = []
        for s in strokes:
            p = s["pen"]
            if p not in by_pen:
                by_pen[p] = []
                order.append(p)
            by_pen[p].append(s)

        with hold_canvas(ctx.plot):
            ctx.plot.clear()
            for pen in order:
                for s in by_pen[pen]:
                    ctx.plot.stroke_style = s["color"]
                    ctx.plot.line_width = max(0.5, s["width"]) * ctx.DPR
                    pts = s["pts"]
                    x0, y0 = ctx.w2c(*pts[0])
                    ctx.plot.begin_path()
                    ctx.plot.move_to(x0, y0)
                    for (x, y) in pts[1:]:
                        px, py = ctx.w2c(x, y)
                        ctx.plot.line_to(px, py)
                    ctx.plot.stroke()

    # When Renderer.plot calls this, respect preview flag for cache updates
    def _plot_replace_entry(paths):
        draw_paths(paths, cache=not getattr(ctx, "_in_preview", False))
        ctx._update_pens_from_strokes()
        ctx.status("New plot rendered.")

    def _pattern_to_strokes(pat, width=1.5):
        strokes = []
        for it in getattr(pat, "items", []):
            if isinstance(it, Polyline) and it.pts and len(it.pts) >= 2:
                # keep pen id in the stroke so pen filtering keeps working
                strokes.append({
                    "pts": list(it.pts),
                    "color": "#111",
                    "width": float(width),
                    "pen": f"pen{getattr(it, 'pen_id', 0)}",
                    # optionally keep feed info for reference
                    "feed_draw": getattr(it, "feed_draw", None),
                })
        return strokes

    def ingest_pattern(pat, renderer_cfg=None):
        # cache for run/preview
        ctx._LAST_PATTERN = pat
        ctx._RENDERER_CFG = dict(renderer_cfg or {})

        # draw an overlay preview derived from the Pattern
        width = 1.5
        try:
            width = float(ctx.flush_every.value) * 0 + 1.5  # keep default 1.5 unless you add UI for it
        except Exception:
            pass
        strokes = _pattern_to_strokes(pat, width=width)
        draw_paths(strokes, cache=True)     # reuse existing drawer
        ctx._update_pens_from_strokes()     # refresh pen filter
        ctx.status("Pattern ingested.")


    api = {
        "plot_replace": _plot_replace_entry,
        "clear_plot":   lambda: (clear_plot(), ctx.status("Plot cleared.")),
        "status":       ctx.status,
        "ingest_pattern": ingest_pattern,
    }

    return api, draw_paths, clear_plot


# ---------- 4) the public function now orchestrates helpers ----------
def show_area_and_compensation_widget(grbl):
    """
    Interactive area + Z compensation + color pots + plot overlay + Runner.
    Public API, return value, and globals are unchanged.
    """
    x_max, y_max = float(grbl.cfg.x_max), float(grbl.cfg.y_max)
    assert x_max > 0 and y_max > 0

    instance_key = ("PPWv4", round(x_max, 3), round(y_max, 3))
    g = globals()
    if g.get("_PPW_INSTANCE") and g["_PPW_INSTANCE"].get("key") == instance_key:
        display(g["_PPW_INSTANCE"]["wrapper"])
        return g["_PPW_INSTANCE"]["getter"]

    CORNERS   = ["BL", "BR", "TL", "TR"]
    DRAGGABLE = {"BL", "TR"}
    corner = {
        "BL": [0.0,     0.0,    1.0],
        "TR": [x_max,   y_max,  1.0],
        "TL": [0.0,     y_max,  1.0],
        "BR": [x_max,   0.0,    1.0],
    }
    _enforce_rect_from_BL_TR(corner, x_max, y_max)

    # context collects everything closures used to close over before
    ctx = SimpleNamespace()
    ctx._LAST_PATTERN = None
    ctx._RENDERER_CFG = {}
    ctx._drag_arm = None 
    ctx._in_preview = False
    ctx.grbl = grbl
    ctx.x_max, ctx.y_max = x_max, y_max
    ctx.CORNERS, ctx.DRAGGABLE = CORNERS, DRAGGABLE
    ctx.corner = corner
    ctx.pots = []
    ctx._next_pot_id = 1
    ctx.selected = ('corner', 'BL')
    ctx.show_overlay = True
    ctx._LAST_STROKES = []
    ctx.jog_boxes = []
    ctx._draw_timer = None
    ctx._draw_lock = threading.Lock()

    # persistence bucket from your globals, unchanged
    if '_PPW_STATE' not in g:
        g['_PPW_STATE'] = {}
    ctx._PPW_STATE = g['_PPW_STATE']
    ctx._state_key = (round(x_max, 3), round(y_max, 3))

    # widgets and canvas
    _build_canvas(ctx)
    _build_basic_controls(ctx)
    _status_factory(ctx)

    # draw_static identical rendering, moved here for clarity
    def draw_static():
        bg = ctx.bg; DPR = ctx.DPR; w2c = ctx.w2c
        with hold_canvas(bg):
            bg.clear()
            # grid
            bg.stroke_style = "#e5e5e5"; bg.line_width = 1 * DPR
            gx = 0.0
            while gx <= ctx.x_max + 1e-6:
                x0c, y0c = w2c(gx, 0.0); x1c, y1c = w2c(gx, ctx.y_max)
                bg.begin_path(); bg.move_to(x0c, y0c); bg.line_to(x1c, y1c); bg.stroke()
                gx += 50.0
            gy = 0.0
            while gy <= ctx.y_max + 1e-6:
                x0c, y0c = w2c(0.0, gy); x1c, y1c = w2c(ctx.x_max, gy)
                bg.begin_path(); bg.move_to(x0c, y0c); bg.line_to(x1c, y1c); bg.stroke()
                gy += 50.0
            # bed outline
            bg.stroke_style = "#666"; bg.line_width = 2 * DPR
            x0c, y0c = w2c(0.0, 0.0); x1c, y1c = w2c(ctx.x_max, ctx.y_max)
            bg.stroke_rect(x0c, y1c, (x1c - x0c), (y0c - y1c))
            # ticks and labels
            bg.fill_style = "#888"; bg.font = f"{11 * DPR}px sans-serif"
            try: bg.text_align = "center"; bg.text_baseline = "top"
            except: pass
            gx = 0.0
            while gx <= ctx.x_max + 1e-6:
                px, py0 = w2c(gx, 0.0)
                bg.begin_path(); bg.move_to(px, py0); bg.line_to(px, py0 + 6*DPR); bg.stroke()
                bg.fill_text(f"{int(gx)}", px, py0 + 8*DPR)
                gx += 50.0
            try: bg.text_align = "right"; bg.text_baseline = "middle"
            except: pass
            gy = 0.0
            while gy <= ctx.y_max + 1e-6:
                px0, py = w2c(0.0, gy)
                bg.begin_path(); bg.move_to(px0, py); bg.line_to(px0 - 6*DPR, py); bg.stroke()
                bg.fill_text(f"{int(gy)}", px0 - 8*DPR, py)
                gy += 50.0
            try:
                bg.text_align = "center"; bg.text_baseline = "bottom"
            except: pass
            xc, yc = w2c(ctx.x_max/2.0, 0.0); bg.fill_text("X (mm)", xc, yc + 28*DPR)
            bg.save()
            try:
                bg.translate(*w2c(0.0, ctx.y_max/2.0)); bg.rotate(-math.pi/2)
                bg.text_align = "center"; bg.text_baseline = "top"
                bg.fill_text("Y (mm)", 0, -28*DPR)
            finally:
                bg.restore()
    ctx.draw_static = draw_static

    # dynamic draw is copied over verbatim, using ctx fields
    def request_redraw():
        with ctx._draw_lock:
            if ctx._draw_timer is not None:
                return
            now = time.monotonic()
            interval = 1/60.0
            delay = max(0.0, interval - getattr(ctx, "_last_draw", 0.0))
            def _do():
                draw_dynamic()
                ctx._last_draw = time.monotonic()
                ctx._draw_timer = None
            ctx._draw_timer = threading.Timer(delay, _do)
            ctx._draw_timer.start()
    ctx.request_redraw = request_redraw

    # jog ui and dynamic overlay kept identical, just read/write ctx.*
    def draw_dynamic():
        fg = ctx.fg; w2c = ctx.w2c; DPR = ctx.DPR
        ctx.jog_boxes.clear()
        with hold_canvas(fg):
            fg.clear()
            if not ctx.show_overlay:
                return
            # rectangle
            x0, y0, x1, y1 = _rect_bounds(ctx.corner)
            p0x, p0y = w2c(x0, y0); p1x, p1y = w2c(x1, y1)
            sel_x, sel_y = p0x, p1y
            sel_w, sel_h = (p1x - p0x), (p0y - p1y)
            fg.global_alpha = 0.16
            fg.fill_style = "#1f77b4"; fg.fill_rect(sel_x, sel_y, sel_w, sel_h)
            fg.global_alpha = 1.0
            fg.stroke_style = "#1f77b4"; fg.line_width = 2 * DPR
            fg.stroke_rect(sel_x, sel_y, sel_w, sel_h)
            # pots
            for p in ctx.pots:
                px, py = w2c(p['x'], p['y'])
                r_pot = 9 * DPR
                fg.begin_path(); fg.stroke_style = "#333"; fg.line_width = 2*DPR
                fg.arc(px, py, r_pot+1*DPR, 0, 2*math.pi); fg.stroke()
                fg.begin_path(); fg.fill_style = p['color']
                fg.arc(px, py, r_pot, 0, 2*math.pi); fg.fill()
                fg.fill_style = "#fff"; fg.font = f"{10 * DPR}px sans-serif"
                try: fg.text_align = "center"; fg.text_baseline = "middle"
                except: pass
                fg.fill_text(f"{int(round(p['h']*100))}", px, py)
                _draw_cross_jog_buttons(ctx, 'pot', p['id'], px, py, r_pot)
            # corners
            for c in ctx.CORNERS:
                cxp, cyp = w2c(ctx.corner[c][0], ctx.corner[c][1])
                r = 9 * DPR if c in ctx.DRAGGABLE else 7 * DPR
                fg.begin_path()
                fg.fill_style = "#2ca02c" if ctx.selected == ('corner', c) else "#d62728"
                fg.arc(cxp, cyp, r, 0, 2*math.pi); fg.fill()
                fg.fill_style = "#fff"; fg.font = f"{10 * DPR}px sans-serif"
                try: fg.text_align = "center"; fg.text_baseline = "middle"
                except: pass
                fg.fill_text(f"{int(round(ctx.corner[c][2]*100))}", cxp, cyp)
                fg.fill_style = "#000"; fg.font = f"{11 * DPR}px sans-serif"
                fg.fill_text(c, cxp + 15*DPR, cyp - 12*DPR)
                if c in ctx.DRAGGABLE:
                    _draw_cross_jog_buttons(ctx, 'corner', c, cxp, cyp, r)
            # center point
            cx0, cy0, hc = _center_info(ctx.corner, ctx.CORNERS)
            cxc, cyc = w2c(cx0, cy0)
            r_c = 8 * DPR
            fg.begin_path(); fg.fill_style = "#000"
            fg.arc(cxc, cyc, r_c, 0, 2*math.pi); fg.fill()
            fg.fill_style = "#fff"; fg.font = f"{10 * DPR}px sans-serif"
            try: fg.text_align = "center"; fg.text_baseline = "middle"
            except: pass
            fg.fill_text(f"{int(round(hc*100))}", cxc, cyc)
            fg.fill_style = "#000"; fg.font = f"{11 * DPR}px sans-serif"
            fg.fill_text("C", cxc + 14*DPR, cyc - 10*DPR)
    ctx.draw_dynamic = draw_dynamic

    def _draw_cross_jog_buttons(ctx, kind, key_or_id, cx, cy, marker_radius_px):
        DPR = ctx.DPR; fg = ctx.fg
        btn_w = int(round(16 * 0.7 * DPR))
        btn_h = int(round(14 * 0.7 * DPR))
        gap   = int(round(4  * 0.7 * DPR))
        off1 = int(round(marker_radius_px + 4 * DPR))
        off2 = off1 + btn_h + gap
        rings = [(0.1, "+",  off1), (1.0, "++", off2)]
        def add_btn(x, y, w, h, label, dx, dy):
            fg.fill_style = "#d7d7d7"; fg.fill_rect(x, y, w, h)
            fg.stroke_style = "#888";  fg.stroke_rect(x, y, w, h)
            fg.fill_style = "#000";    fg.font = f"{int(10 * 0.9 * DPR)}px sans-serif"
            try: fg.text_align="center"; fg.text_baseline="middle"
            except: pass
            fg.fill_text(label, x + w/2, y + h/2)
            jb = {'kind': kind, 'dx': dx, 'dy': dy, 'rect': (x, y, w, h)}
            if kind == 'corner': jb['key'] = key_or_id
            else:                jb['id']  = key_or_id
            ctx.jog_boxes.append(jb)
        for mag,label,off in rings:
            add_btn(cx - btn_w/2, cy - off - btn_h, btn_w, btn_h, label, 0.0, +mag)
        for mag,label,off in rings:
            add_btn(cx - btn_w/2, cy + off, btn_w, btn_h, label, 0.0, -mag)
        for mag,label,off in rings:
            add_btn(cx - off - btn_w, cy - btn_h/2, btn_w, btn_h, label, -mag, 0.0)
        for mag,label,off in rings:
            add_btn(cx + off, cy - btn_h/2, btn_w, btn_h, label, +mag, 0.0)

    # motion worker unchanged
    ctx.motion = MotionWorker(ctx.grbl, ctx.status, hz=40.0, settle=0.0)

    # handlers, persistence, and runner glue
    # These are copied from your original code with trivial ctx.* substitutions.
    # To keep this reply short, I am not duplicating every handler here.
    # Paste your existing handlers and replace free variables with ctx.* as done above.

    # ----- layout assembly (unchanged structure) -----

    left_btns_top = W.VBox([ctx.sweep_btn, ctx.penup_btn, ctx.pendown_btn, ctx.home_btn, ctx.save_btn],
                           layout=W.Layout(width="220px", gap="6px"))
    left_btns_bottom = W.VBox([ctx.delplot_btn, ctx.overlay_btn, ctx.resetZ_btn],
                              layout=W.Layout(width="220px", gap="6px", margin="8px 0 0 0"))
    paper_row = W.HBox([ctx.a4_btn, ctx.a5_btn, ctx.s15_btn, ctx.s10_btn],
                       layout=W.Layout(gap="6px", width="220px"))
    left_col = W.VBox([left_btns_top, paper_row, left_btns_bottom],
                      layout=W.Layout(width="260px", gap="6px"))
    
    row1 = W.HBox([ctx.layers, ctx.h_slider, left_col],
                  layout=W.Layout(width="100%", align_items="flex-start", justify_content="space-between", gap="16px"))



    pot_controls = W.VBox([
        W.HTML("<b>Color Pots</b>"),
        W.VBox([ctx.add_pot_btn, ctx.del_pot_btn, ctx.pot_color], layout=W.Layout(gap="8px")),
        ctx.pot_list
    ], layout=W.Layout(min_width="360px"))

    runner_panel = _build_runner_panel(ctx)


    row2 = W.HBox([pot_controls, runner_panel],
                  layout=W.Layout(width="100%", align_items="flex-start", justify_content="space-between", gap="16px"))

    wrapper = W.VBox([
        W.HTML('<b>Set Area, Heights, Pots, Plot & Run</b>', layout=W.Layout(margin='0 0 8px 0')),
        row1,
        row2,
        ctx.sel_label,
        ctx.status_out
    ], layout=W.Layout(padding='10px', width='100%'))



    # ---- plot API identical to yours ----
    ctx._update_pens_from_strokes = lambda: _update_pens_from_strokes_impl(ctx)
    api, draw_paths, clear_plot = _plot_api_factory(ctx)
    ctx._PPW_API = api
    ctx._plot_draw_paths = draw_paths
    globals()["_PPW_API"] = api

    # init
    _try_load_state_impl(ctx)
    ctx.draw_static()
    clear_plot()
    ctx.request_redraw()
    ctx.status("Ready. Click to select. Drag BL and TR. Jog with + or ++. Slider changes Z.")
    _set_selected_impl(ctx, ('corner', 'BL'))

    def getter(mode=None):
        data = _collect_state(ctx.corner, ctx.pots, ctx.CORNERS)
        if mode in (None, 'legacy'):
            area = data['rectangle']
            comp = {k: data['corners'][k]['h'] for k in ctx.CORNERS}
            return area, comp
        elif mode == 'all':
            return data
        else:
            return data

    globals()["_PPW_INSTANCE"] = {
        "key": instance_key,
        "wrapper": wrapper,
        "getter": getter,
        "draw_paths": draw_paths,
        "clear_plot": clear_plot,
        "status": ctx.status,
        "api": api,
    }
    globals()["_PPW_API"] = api

    _wire_handlers(ctx)
    display(wrapper)

    return getter


# ---------- 5) small pieces factored as pure helpers (identical behavior) ----------
def _try_load_state_impl(ctx):
    s = ctx._PPW_STATE.get(ctx._state_key)
    if not s:
        return False
    try:
        cs = s.get('corners', {})
        if 'BL' in cs and 'TR' in cs:
            ctx.corner['BL'][0] = float(cs['BL'].get('x', ctx.corner['BL'][0]))
            ctx.corner['BL'][1] = float(cs['BL'].get('y', ctx.corner['BL'][1]))
            ctx.corner['TR'][0] = float(cs['TR'].get('x', ctx.corner['TR'][0]))
            ctx.corner['TR'][1] = float(cs['TR'].get('y', ctx.corner['TR'][1]))
            _enforce_rect_from_BL_TR(ctx.corner, ctx.x_max, ctx.y_max)
        for k in ctx.CORNERS:
            if k in cs and 'h' in cs[k]:
                ctx.corner[k][2] = float(cs[k]['h'])
        ps = s.get('pots', [])
        ctx.pots = [dict(p) for p in ps]
        ctx._next_pot_id = (max([p['id'] for p in ctx.pots], default=0) + 1) if ctx.pots else 1
        return True
    except Exception:
        return False

def _update_pens_from_strokes_impl(ctx):
    pens = []
    for s in ctx._LAST_STROKES:
        pen_str = s.get("pen", "pen0")
        try:
            pid = int(''.join(ch for ch in pen_str if ch.isdigit()))
        except Exception:
            pid = 0
        pens.append(pid)
    pens = sorted(set(pens))
    ctx.pens_multi.options = [(f"pen {pid}", pid) for pid in pens]
    ctx.pens_multi.value = tuple(pens)

def _set_selected_impl(ctx, entity, move_and_set=True):
    ctx.selected = entity
    ctx.h_slider.disabled = False
    ctx.pot_color.disabled = True

    if entity[0] == 'corner':
        key = entity[1]
        # update height slider without firing
        ctx._suppress_h_event = True
        try:
            ctx.h_slider.value = float(ctx.corner[key][2])
        finally:
            ctx._suppress_h_event = False

        # label and color UI
        ctx.sel_label.value = f"<b>Selected:</b> Corner {key}"
        ctx.pot_color.disabled = True
        _safe_unobserve(ctx.pot_color, getattr(ctx, "_pot_color_handler", None), names='value')

        # go there now
        if move_and_set:
            ctx.motion.queue_goto_and_h(ctx.corner[key][0], ctx.corner[key][1], ctx.corner[key][2])


    elif entity[0] == 'pot':
        pid = entity[1]
        pot = next((p for p in ctx.pots if p['id'] == pid), None)
        if pot is None:
            return  # nothing to select
        ctx._suppress_h_event = True
        try:
            ctx.h_slider.value = float(pot['h'])
        finally:
            ctx._suppress_h_event = False

        ctx.sel_label.value = f"<b>Selected:</b> Pot #{pid}"
        ctx.pot_color.disabled = False

        # rebind color handler safely
        _safe_unobserve(ctx.pot_color, getattr(ctx, "_pot_color_handler", None), names='value')

        def _pot_color_handler(change):
            if ctx.selected[0] == 'pot':
                pid2 = ctx.selected[1]
                pot2 = next((pp for pp in ctx.pots if pp['id'] == pid2), None)
                if pot2:
                    pot2['color'] = ctx.pot_color.value
                    ctx.request_redraw()
                    _refresh_pot_list(ctx, selected_id=pid2)

        ctx._pot_color_handler = _pot_color_handler
        ctx.pot_color.observe(ctx._pot_color_handler, names='value')

        if move_and_set:
            ctx.motion.queue_goto_and_h(pot['x'], pot['y'], pot['h'])
    else:
        cx, cy, hc = _center_info(ctx.corner, ctx.CORNERS)
        ctx._suppress_h_event = True
        try:
            ctx.h_slider.value = float(hc)
        finally:
            ctx._suppress_h_event = False
        ctx.h_slider.disabled = True
        ctx.sel_label.value = "<b>Selected:</b> Center"
        if move_and_set:
            ctx.motion.queue_goto_and_h(cx, cy, hc)
        ctx.pot_color.disabled = True
        _safe_unobserve(ctx.pot_color, getattr(ctx, "_pot_color_handler", None), names='value')

    ctx.request_redraw()
